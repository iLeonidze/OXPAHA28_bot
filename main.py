import time
from threading import Timer
from os.path import isfile
from typing import Dict, Any, List

import telegram.helpers
from yaml import safe_load as yaml_safe_load
from yaml import safe_dump as yaml_safe_dump
import logging
from telegram import Update, Bot, ReplyKeyboardMarkup, KeyboardButton, PhotoSize, \
    InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, ApplicationBuilder, CommandHandler, MessageHandler, filters

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

CONTEXT: dict
IS_CONTEXT_CHANGED = False
CONFIG: dict
BOT: Bot


def get_current_timestamp() -> int:
    return round(time.time() * 1000)


def encode_markdown(string: str) -> str:
    return telegram.helpers.escape_markdown(string, version=2)


def set_interval(func, sec):
    def func_wrapper():
        set_interval(func, sec)
        func()

    t = Timer(sec, func_wrapper)
    t.start()
    return t


async def send_request_to_main_group(update: Update, dry_run=False):
    user_context = get_user_context(update)

    issue_type = user_context.get('selected_category')
    issue_area = user_context.get('selected_problem_area')
    issue_description = encode_markdown(user_context.get('selected_details', 'не указано'))

    if update.effective_user.username:
        issue_username = '@' + encode_markdown(update.effective_user.username)
    else:
        issue_username = '[' + encode_markdown(
            update.effective_user.first_name) + '](tg://user?id=' + str(
            update.effective_user.id) + ')'

    if 'пожар' in issue_type.lower():
        issue_area = 'в секции'

    issue_address = 'ул\\. ' + user_context.get('selected_street') + \
                    ', дом ' + str(user_context.get('selected_house'))

    if user_context.get('selected_section'):
        issue_address += ', секция ' + str(user_context.get('selected_section'))

    if user_context.get('selected_floor'):
        issue_address += ', этаж ' + str(user_context.get('selected_floor'))

    if user_context.get('selected_flat'):
        issue_address += ', кв\\. ' + str(user_context.get('selected_flat'))

    if user_context.get('selected_storeroom'):
        issue_address += ', кл\\. ' + str(user_context.get('selected_storeroom'))

    if user_context.get('selected_parking'):
        issue_address += ', мм\\. ' + str(user_context.get('selected_parking'))

    message = CONFIG['messages_templates']['request'].format(
        type=issue_type,
        area=issue_area,
        address=issue_address,
        username=issue_username,
        description=issue_description
    )

    message = message.replace('\n ', '\n')

    photo = None
    file_id = user_context.get('photo_file_id')
    if file_id:
        file_unique_id = user_context.get('photo_file_unique_id')
        file_size = user_context.get('photo_file_size')
        height = user_context.get('photo_height')
        width = user_context.get('photo_width')

        photo = PhotoSize(file_id=file_id,
                          file_unique_id=file_unique_id,
                          file_size=file_size,
                          height=height,
                          width=width)

    if dry_run:
        return message, photo

    message_details = await BOT.send_message(text=message,
                                             chat_id=CONFIG['groups']['main']['id'],
                                             parse_mode='MarkdownV2')
    message_id = message_details.message_id

    if photo:
        await BOT.send_photo(photo=photo,
                             chat_id=CONFIG['groups']['main']['id'],
                             reply_to_message_id=message_id,
                             disable_notification=True)

    return message_id


async def send_success_message_for_user(update: Update, message_id: int) -> None:
    message_link = encode_markdown(CONFIG['groups']['main']['public_link'] + '/' + str(message_id))

    message = CONFIG['messages_templates']['success'].format(
        message_link=message_link
    )

    keyboard = form_initial_keyboard()

    await BOT.send_message(text=message,
                           chat_id=update.effective_chat.id,
                           parse_mode='MarkdownV2',
                           reply_markup=keyboard)

    if 'пожар' in get_user_context(update).get('selected_category').lower():
        await BOT.send_message(text=CONFIG['messages_templates']['request_fire_hint'],
                               chat_id=update.effective_chat.id)


def get_user_context(update: Update) -> Dict:
    user_id = update.effective_chat.id
    return CONTEXT['users'].get(user_id, {})


def set_user_context(update: Update, user_context: Dict) -> None:
    user_id = update.effective_chat.id
    CONTEXT['users'][user_id] = user_context
    global IS_CONTEXT_CHANGED
    IS_CONTEXT_CHANGED = True


def update_user_context(update: Update, key: str, value: Any, overwrite=True) -> None:
    user_context = get_user_context(update)
    if not user_context.get(key) or overwrite:
        user_context[key] = value
        set_user_context(update, user_context)


def reset_user_context(update: Update) -> None:
    old_user_context = get_user_context(update)
    set_user_context(update, {
        'bot_started': old_user_context['bot_started'],
        'dialog_state': 'start',
        'dialog_state_updated': get_current_timestamp(),
        'requests_history': old_user_context.get('requests_history', []),
        'last_request': old_user_context.get('last_request')
    })


def update_user_requests_history(update: Update, message_id: int) -> None:
    requests_history = list(get_user_context(update).get('requests_history', []))
    requests_history.append(message_id)
    update_user_context(update, 'requests_history', requests_history)
    update_user_context(update, 'last_request', get_current_timestamp())


def form_keyboard(buttons: List, add_control_buttons=True) -> ReplyKeyboardMarkup:
    buttons_rows = buttons
    if add_control_buttons:
        buttons_rows = buttons_rows + CONFIG['keyphrases']['control_buttons']

    buttons_list = []
    for item in buttons_rows:
        buttons_list.append([KeyboardButton(item)])
    return ReplyKeyboardMarkup(buttons_list, resize_keyboard=False, one_time_keyboard=True)


def form_initial_keyboard() -> ReplyKeyboardMarkup:
    buttons_list = []
    for item in CONFIG['keyphrases']['issues_categories']:
        buttons_list.append([KeyboardButton(item)])

    control_buttons = []
    for item in CONFIG['keyphrases']['special_buttons']:
        control_buttons.append(KeyboardButton(item))

    buttons_list.append(control_buttons)

    return ReplyKeyboardMarkup(buttons_list, resize_keyboard=False, one_time_keyboard=True)


def get_dialog_state(update: Update) -> str:
    return get_user_context(update).get('dialog_state', None)


async def update_dialog_state(update, state) -> None:
    old_current_state = get_dialog_state(update)
    if old_current_state and old_current_state != state:
        states_history = get_user_context(update).get('dialog_states_history', [])
        if not states_history or states_history[-1] != old_current_state:
            states_history.append(old_current_state)
            update_user_context(update, 'dialog_states_history', states_history)

    update_user_context(update, 'dialog_state', state)
    update_user_context(update, 'dialog_state_updated', get_current_timestamp())
    chat_id = update.effective_chat.id

    if state == 'start':
        keyboard = form_initial_keyboard()
        await BOT.send_message(chat_id=chat_id,
                               text=CONFIG['messages_templates']['start'],
                               reply_markup=keyboard)
        return

    if state == 'select_street':
        keyboard = form_keyboard(CONFIG['keyphrases']['supported_streets'])
        await BOT.send_message(chat_id=chat_id,
                               text=CONFIG['messages_templates']['select_street'],
                               reply_markup=keyboard)
        return

    if state == 'select_house_number':
        await BOT.send_message(chat_id=chat_id,
                               text=CONFIG['messages_templates']['select_house_number'])
        return

    if state == 'select_problem_area':
        keyboard = form_keyboard(CONFIG['keyphrases']['problem_areas'])
        await BOT.send_message(chat_id=chat_id,
                               text=CONFIG['messages_templates']['select_problem_area'],
                               reply_markup=keyboard)
        return

    elif state == 'select_section_number':
        await BOT.send_message(chat_id=chat_id,
                               text=CONFIG['messages_templates']['select_section_number'])

    if state == 'select_floor_number':
        await BOT.send_message(chat_id=chat_id,
                               text=CONFIG['messages_templates']['select_floor_number'])
        return

    if state == 'select_flat_number':
        await BOT.send_message(chat_id=chat_id,
                               text=CONFIG['messages_templates']['select_flat_number'])
        return

    if state == 'select_storeroom_number':
        await BOT.send_message(chat_id=chat_id,
                               text=CONFIG['messages_templates']['select_storeroom_number'])
        return

    if state == 'select_parking_number':
        await BOT.send_message(chat_id=chat_id,
                               text=CONFIG['messages_templates']['select_parking_number'])
        return

    if state == 'specify_description':
        await BOT.send_message(chat_id=chat_id,
                               text=CONFIG['messages_templates']['specify_description'])
        return

    if state == 'confirm':
        keyboard = form_keyboard(CONFIG['keyphrases']['confirmation'])

        request_body, photo = await send_request_to_main_group(update, dry_run=True)
        message = encode_markdown(CONFIG['messages_templates']['confirm_request']) + '\n\n' + \
                  "\n".join(request_body.split('\n')[:-1])

        await BOT.send_message(chat_id=chat_id,
                               text=message,
                               parse_mode='MarkdownV2',
                               reply_markup=keyboard)

        if photo:
            await BOT.send_photo(photo=photo,
                                 chat_id=chat_id,
                                 disable_notification=True)

        return

    if state == 'upload_photo':
        await BOT.send_message(chat_id=chat_id,
                               text=CONFIG['messages_templates']['upload_photo'])
        return


async def proceed_fallback(update: Update, last_state) -> None:
    await BOT.send_message(chat_id=update.effective_chat.id,
                           text=CONFIG['messages_templates']['fallback'])
    await update_dialog_state(update, last_state)


async def start(update: Update, _):
    # ignore any messages from non-personal dialogs
    if update.effective_chat.type != 'private':
        return

    update_user_context(update, 'bot_started', get_current_timestamp(), overwrite=False)
    reset_user_context(update)

    await BOT.send_message(chat_id=update.effective_chat.id,
                           text=CONFIG['messages_templates']['welcome'])
    await update_dialog_state(update, 'start')


async def send_pin_message(update: Update, _):
    # ignore any messages from non-personal dialogs
    if update.effective_chat.type != 'private':
        return

    # TODO: add security to send message only when superuser called this command

    bot_username = CONFIG['bot_credentials']['username']
    inline_keyboard_markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(CONFIG['messages_templates']['pin_message_button'],
                                 url=f'tg://resolve?domain={bot_username}&start=from_channel'),
        ],
    ])

    text = CONFIG['messages_templates']['pin_message'] + '\n\n' + CONFIG['messages_templates']['rules']

    await BOT.send_message(chat_id=CONFIG['groups']['main']['id'],
                           text=text,
                           reply_markup=inline_keyboard_markup)


def is_go_back_message(message):
    if not message:
        return False
    message = message.lower()
    for phrase in CONFIG['keyphrases']['go_back']:
        if phrase in message:
            return True
    return False


def is_go_restart_message(message):
    if not message:
        return False
    message = message.lower()
    for phrase in CONFIG['keyphrases']['go_restart']:
        if phrase in message:
            return True
    return False


def is_go_confirm_message(message):
    message = message.lower()
    for phrase in CONFIG['keyphrases']['go_confirm']:
        if phrase in message:
            return True
    return False


async def go_back(update):
    dialog_states_history = get_user_context(update).get('dialog_states_history', [])
    if not dialog_states_history:
        await update_dialog_state(update, 'start')
        return

    previous_dialog_state = dialog_states_history.pop()
    dialog_states_history = list(dialog_states_history)
    await update_dialog_state(update, previous_dialog_state)
    update_user_context(update, 'dialog_states_history', dialog_states_history)


async def go_restart(update) -> None:
    await update_dialog_state(update, 'start')
    reset_user_context(update)


async def proceed_group_chat_message(update: Update) -> None:
    if update.effective_message.from_user.id in CONFIG['responsible_persons'] \
            and update.effective_message.reply_to_message is not None \
            and update.effective_message.reply_to_message.forward_from_message_id is not None:
        for user_id, user_data in CONTEXT['users'].items():
            if user_data.get('requests_history') is not None and update.effective_message.reply_to_message.forward_from_message_id in user_data['requests_history']:
                message = CONFIG['messages_templates']['received_response_from_responsible_person'] + update.effective_message.text + '\n\n' + update.effective_message.link
                await BOT.send_message(text=message,
                                       chat_id=user_id)
                return


async def proceed_user_message(update: Update, _) -> None:
    if update.effective_chat.id == CONFIG['groups']['chat']['id']:
        return await proceed_group_chat_message(update)

    if update.effective_chat.type != 'private':
        return

    dialog_state = get_dialog_state(update)

    message = None
    if update.effective_message.text:
        message = update.effective_message.text.strip()

    if is_go_back_message(message):
        await go_back(update)
        return

    if is_go_restart_message(message):
        await go_restart(update)
        return

    if dialog_state is None:
        await update_dialog_state(update, 'start')
        return

    # user selected request type -> validate type -> ask to select street
    if dialog_state == 'start':
        if 'правила' in message.lower():
            await BOT.send_message(text=CONFIG['messages_templates']['rules'],
                                   chat_id=update.effective_chat.id)
            return

        if 'контакты' in message.lower():
            await BOT.send_message(text=CONFIG['messages_templates']['contacts'],
                                   chat_id=update.effective_chat.id)
            return

        if not message or message not in CONFIG['keyphrases']['issues_categories']:
            await proceed_fallback(update, dialog_state)
            return

        update_user_context(update, 'selected_category', message)
        await update_dialog_state(update, 'select_street')
        return

    # user selected street -> validate street -> ask to type house number
    if dialog_state == 'select_street':
        if not message or message not in CONFIG['keyphrases']['supported_streets']:
            await proceed_fallback(update, dialog_state)
            return

        update_user_context(update, 'selected_street', message)
        await update_dialog_state(update, 'select_house_number')
        return

    # user typed house number -> validate number ->
    #    - if "пожар" in selected_category -> ask section, details
    #    - otherwise ask to select problem area, etc.
    if dialog_state == 'select_house_number':
        if not message or not message.isnumeric():
            await proceed_fallback(update, dialog_state)
            return

        house_number = int(message)
        if house_number < 1 or house_number > 50:
            await proceed_fallback(update, dialog_state)
            return

        update_user_context(update, 'selected_house', house_number)

        if 'пожар' in get_user_context(update).get('selected_category').lower():
            await update_dialog_state(update, 'select_section_number')
        else:
            await update_dialog_state(update, 'select_problem_area')
        return

    # user selected problem area -> validate problem area ->
    #    - if user selected "на этаже" -> ask section, floor, confirm
    #    - if user selected "у квартиры" -> ask section, floor, flat, confirm
    #    - if user selected "на паркинге" -> ask parking number, confirm
    #    - if user selected "в кладовках" -> ask storeroom number, confirm
    #    - if user selected "во внутреннем дворе" -> ask details
    #    - if user selected "на улице у дома" -> ask details
    if dialog_state == 'select_problem_area':
        if not message or message not in CONFIG['keyphrases']['problem_areas']:
            await proceed_fallback(update, dialog_state)
            return

        message = message.lower()
        update_user_context(update, 'selected_problem_area', message)

        if 'этаж' in message:
            await update_dialog_state(update, 'select_section_number')
        elif 'квартир' in message:
            await update_dialog_state(update, 'select_section_number')
        elif 'парк' in message:
            await update_dialog_state(update, 'select_parking_number')
        elif 'кладовк' in message:
            await update_dialog_state(update, 'select_storeroom_number')
        elif 'двор' in message:
            await update_dialog_state(update, 'specify_description')
        elif 'улиц' in message:
            await update_dialog_state(update, 'specify_description')

        return

    # user specified section number -> validate section number ->
    #    - if "пожар" in selected_category -> confirm
    #    - otherwise ask floor, etc.
    if dialog_state == 'select_section_number':
        if not message or not message.isnumeric():
            await proceed_fallback(update, dialog_state)
            return

        section_number = int(message)
        if section_number < 1 or section_number > 9:
            await proceed_fallback(update, dialog_state)
            return

        update_user_context(update, 'selected_section', section_number)

        if 'пожар' in get_user_context(update).get('selected_category').lower():
            await update_dialog_state(update, 'confirm')
        else:
            await update_dialog_state(update, 'select_floor_number')

        return

    # user specified floor number -> validate floor number ->
    #    - if user selected "у квартиры" -> ask flat, details
    #    - otherwise details
    if dialog_state == 'select_floor_number':
        if not message or not message.isnumeric():
            await proceed_fallback(update, dialog_state)
            return

        floor_number = int(message)
        if floor_number < -1 or floor_number > 30:
            await proceed_fallback(update, dialog_state)
            return

        update_user_context(update, 'selected_floor', floor_number)
        await update_dialog_state(update, 'select_flat_number')
        return

    # user specified flat number -> validate flat number -> confirm
    if dialog_state == 'select_flat_number':
        if not message or not message.isnumeric():
            await proceed_fallback(update, dialog_state)
            return

        flat_number = int(message)
        if flat_number < 1 or flat_number > 700:
            await proceed_fallback(update, dialog_state)
            return

        update_user_context(update, 'selected_flat', flat_number)
        await update_dialog_state(update, 'confirm')
        return

    # user specified parking number -> validate parking number -> confirm
    if dialog_state == 'select_parking_number':
        if not message or not message.isnumeric():
            await proceed_fallback(update, dialog_state)
            return

        parking_number = int(message)
        if parking_number < 1 or parking_number > 500:
            await proceed_fallback(update, dialog_state)
            return

        update_user_context(update, 'selected_parking', parking_number)
        await update_dialog_state(update, 'confirm')
        return

    # user specified storeroom number -> validate storeroom number -> confirm
    if dialog_state == 'select_storeroom_number':
        if not message or not message.isnumeric():
            await proceed_fallback(update, dialog_state)
            return

        storeroom_number = int(message)
        if storeroom_number < 1 or storeroom_number > 500:
            await proceed_fallback(update, dialog_state)
            return

        update_user_context(update, 'selected_storeroom', storeroom_number)
        await update_dialog_state(update, 'confirm')
        return

    # user specified description -> confirm ->
    #    -> upload photo & send request
    #    -> send request
    if dialog_state == 'specify_description':
        # TODO: support bad words?
        if not message:
            await proceed_fallback(update, dialog_state)
            return

        update_user_context(update, 'selected_details', message[:500])
        await update_dialog_state(update, 'confirm')
        return

    # user confirmed or clicked photo upload
    if dialog_state == 'confirm':
        if not message:
            await proceed_fallback(update, dialog_state)
            return

        if "фото" in message.lower():
            await update_dialog_state(update, 'upload_photo')
            return

        if "опис" in message.lower():
            await update_dialog_state(update, 'specify_description')
            return

        if is_go_confirm_message(message):
            request_message_id = await send_request_to_main_group(update)
            update_user_requests_history(update, request_message_id)
            await send_success_message_for_user(update, request_message_id)
            reset_user_context(update)
            save_context()
            return

    if dialog_state == 'upload_photo':
        if not update.effective_message.photo:
            await proceed_fallback(update, dialog_state)
            return

        file_id = update.effective_message.photo[-1].file_id
        file_unique_id = update.effective_message.photo[-1].file_unique_id
        file_size = update.effective_message.photo[-1].file_size
        height = update.effective_message.photo[-1].height
        width = update.effective_message.photo[-1].width

        update_user_context(update, 'photo_file_id', file_id)
        update_user_context(update, 'photo_file_unique_id', file_unique_id)
        update_user_context(update, 'photo_file_size', file_size)
        update_user_context(update, 'photo_height', height)
        update_user_context(update, 'photo_width', width)

        await update_dialog_state(update, 'confirm')
        return


def save_context():
    global IS_CONTEXT_CHANGED
    if IS_CONTEXT_CHANGED:
        with open('context.yaml', 'w') as file:
            IS_CONTEXT_CHANGED = False
            yaml_safe_dump(CONTEXT, file, encoding='UTF-8', allow_unicode=True)


def main():
    if not isfile('config.yaml'):
        raise FileNotFoundError('Configuration file is not exists')

    with open('config.yaml', 'r') as file:
        global CONFIG
        CONFIG = yaml_safe_load(file)

    logging.info('Configuration loaded')

    global CONTEXT
    if not isfile('context.yaml'):
        CONTEXT = {
            'users': {}
        }
    else:
        with open('context.yaml', 'r') as file:
            CONTEXT = yaml_safe_load(file)

    logging.info('Context loaded')

    application: Application = ApplicationBuilder(). \
        token(CONFIG['bot_credentials']['secret']).build()

    # welcome message
    start_handler = CommandHandler('start', start)
    application.add_handler(start_handler)

    # technical command - send pin message
    send_pin_message_handler = CommandHandler('send_pin_message', send_pin_message)
    application.add_handler(send_pin_message_handler)

    # any raw messages from users
    # TODO: add filter only private messages
    messages_handler = MessageHandler((filters.TEXT | filters.PHOTO) & (~filters.COMMAND), proceed_user_message)
    application.add_handler(messages_handler)

    global BOT
    BOT = application.bot

    set_interval(save_context, 10)

    logging.info('Bot is ready, polling...')

    application.run_polling()


if __name__ == '__main__':
    main()