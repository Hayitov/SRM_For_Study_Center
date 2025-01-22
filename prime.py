import logging
import gspread
import json
from oauth2client.service_account import ServiceAccountCredentials
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.dispatcher.router import Router
import asyncio
from datetime import datetime
import pytz
import re
from aiogram.filters import Command

################################################################################
# 1) Admins & (Optional) Teachers
################################################################################
# TWO admins (replace with actual Telegram user IDs)
ADMIN_IDS = [123456789, 987654321]  

# If you also need teacher roles:
TEACHER_IDS = [555444333, 777888999]

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def is_teacher(user_id: int) -> bool:
    return user_id in TEACHER_IDS


################################################################################
# 2) Text Parsing & Similarity Utilities
################################################################################

def parse_text(raw_text: str) -> str:
    """
    Remove punctuation/numbers, convert to lowercase, and return a space-separated string.
    Example:
        Input:  "1. a\n2. b\n3. Word, test!"
        Output: "a b word test"
    """
    # 1) Remove punctuation except whitespace/letters
    only_letters = re.sub(r"[^a-zA-Z\s]+", "", raw_text)

    # 2) Convert to lowercase
    lower_text = only_letters.lower()

    # 3) Remove numeric prefixes line by line
    lines = lower_text.split("\n")
    cleaned_lines = []
    for line in lines:
        line = re.sub(r"^\d+(\.|-|\))?\s*", "", line.strip())
        if line:
            cleaned_lines.append(line)

    # 4) Return space-joined
    return " ".join(cleaned_lines)

def calculate_similarity(student_text: str, teacher_text: str) -> float:
    """
    Return the fraction of overlap between student tokens and teacher tokens.
    E.g., 0.0 -> no overlap, 1.0 -> full overlap.
    """
    student_tokens = set(student_text.split())
    teacher_tokens = set(teacher_text.split())
    if not teacher_tokens:
        return 0.0
    overlap = student_tokens.intersection(teacher_tokens)
    similarity = len(overlap) / len(teacher_tokens)
    return similarity


################################################################################
# 3) Bot & Google Sheets Initialization
################################################################################

# Replace with your actual bot token (or load via environment variables)
BOT_TOKEN = "REPLACE_BOT_TOKEN"

# Replace with your actual scope
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]

# Replace with the path to your Google service-account JSON, or use an environment variable
creds = ServiceAccountCredentials.from_json_keyfile_name(
    "google.json",  # <--- replaced filename here
    scope
)
client = gspread.authorize(creds)

# Registration sheet
# Replace with your actual Google Sheet ID
sheet = client.open_by_key("REPLACE_SHEET_ID").sheet1

# Another sheet (for top list or other data)
sheet2 = client.open_by_key("REPLACE_SHEET2_ID").sheet1

# Replace with your group chat ID
GROUP_CHAT_ID = -999999999

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()

################################################################################
# Generate line-by-line correctness report
################################################################################
def generate_line_by_line_report(teacher_raw: str, student_raw: str) -> str:
    """
    Compare teacher's lines vs student's lines one-by-one.
    Return a string showing which line is correct (✅) or wrong (❌).
    """
    teacher_lines = teacher_raw.splitlines()
    student_lines = student_raw.splitlines()

    max_len = max(len(teacher_lines), len(student_lines))
    report_lines = []

    for i in range(max_len):
        # Teacher line (raw) or blank if missing
        t_line_raw = teacher_lines[i] if i < len(teacher_lines) else ""
        # Student line (raw) or blank if missing
        s_line_raw = student_lines[i] if i < len(student_lines) else ""

        # For matching, parse them
        t_line_parsed = parse_text(t_line_raw)
        s_line_parsed = parse_text(s_line_raw)

        # If parsed lines match exactly (non-empty), consider correct
        is_correct = (t_line_parsed == s_line_parsed and t_line_parsed.strip() != "")

        line_number_label = f"{i+1}."
        status_symbol = "✅" if is_correct else "❌"
        # Show "1. e --> ❌"
        line_text = f"{line_number_label} {s_line_raw.strip()} --> {status_symbol}"

        report_lines.append(line_text)

    return "\n".join(report_lines)


################################################################################
# CHANGE #1: Remove the "Re-submit" button, keep only "/menu"
################################################################################
def menu_only_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="/menu")]
        ],
        resize_keyboard=True
    )


################################################################################
# 4) Registration State Machine & Handlers
################################################################################

class Registration(StatesGroup):
    waiting_for_name = State()
    waiting_for_phone = State()
    waiting_for_additional_phone = State()
    waiting_for_dob = State()
    waiting_for_region = State()
    waiting_for_mode_of_study = State()
    waiting_for_hw_frequency = State()
    waiting_for_referral = State()
    editing_information = State()
    editing_field = State()

def generate_unique_id(sheet, column_name="Unique ID", prefix="V3"):
    column_data = sheet.col_values(sheet.row_values(1).index(column_name) + 1)
    if len(column_data) > 1:  # Exclude the header
        last_id = column_data[-1]
        number = int(last_id[len(prefix):]) + 1
    else:
        number = 1
    return f"{prefix}{number:03}"

def find_column_indices(sheet, headers):
    sheet_headers = sheet.row_values(1)
    return {header: sheet_headers.index(header) + 1 for header in headers}

def is_user_registered(sheet, telegram_id):
    telegram_id_column = sheet.col_values(sheet.row_values(1).index("Telegram ID") + 1)
    return str(telegram_id) in telegram_id_column

def back_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Back")]],
        resize_keyboard=True
    )

@router.message(Command(commands=["start"]))
async def cmd_start(message: types.Message, state: FSMContext):
    if is_user_registered(sheet, message.from_user.id):
        await message.answer("You have already registered. Use /menu to open the main page.")
        return
    await message.answer("Welcome! Please provide your full name (e.g., John Doe):", reply_markup=back_keyboard())
    await state.set_state(Registration.waiting_for_name)

@router.message(Registration.waiting_for_name)
async def process_name(message: types.Message, state: FSMContext):
    if message.text == "Back":
        await message.answer("You are at the start of the registration process.")
        return

    name_parts = message.text.split()
    if len(name_parts) < 2 or not all(part.replace("'", "").isalpha() for part in name_parts):
        await message.answer(
            "Invalid name. Please provide your full name (e.g., John Doe). Ensure it contains at least two words and only letters or the ' symbol.",
            reply_markup=back_keyboard()
        )
        return
    await state.update_data(name=message.text)

    phone_kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Share Phone Number", request_contact=True)], [KeyboardButton(text="Back")]],
        resize_keyboard=True
    )
    await message.answer("Please share your phone number:", reply_markup=phone_kb)
    await state.set_state(Registration.waiting_for_phone)

@router.message(Registration.waiting_for_phone, F.contact)
async def process_phone(message: types.Message, state: FSMContext):
    await state.update_data(phone=message.contact.phone_number)
    await message.answer(
        "Would you like to provide an additional phone number? If yes, please share it. If not, reply with 'No'.",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="Back")]],
            resize_keyboard=True
        )
    )
    await state.set_state(Registration.waiting_for_additional_phone)

@router.message(Registration.waiting_for_phone)
async def back_from_phone(message: types.Message, state: FSMContext):
    if message.text == "Back":
        await message.answer("Welcome! Please provide your full name (e.g., John Doe):", reply_markup=back_keyboard())
        await state.set_state(Registration.waiting_for_name)

@router.message(Registration.waiting_for_additional_phone)
async def process_additional_phone(message: types.Message, state: FSMContext):
    if message.text == "Back":
        phone_kb = ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="Share Phone Number", request_contact=True)], [KeyboardButton(text="Back")]],
            resize_keyboard=True
        )
        await message.answer("Please share your phone number:", reply_markup=phone_kb)
        await state.set_state(Registration.waiting_for_phone)
        return

    if message.text.lower() == 'no':
        await state.update_data(additional_phone="Not Provided")
    else:
        await state.update_data(additional_phone=message.text)

    await message.answer("Please provide your date of birth (DD/MM/YYYY):", reply_markup=back_keyboard())
    await state.set_state(Registration.waiting_for_dob)

@router.message(Registration.waiting_for_dob)
async def process_dob(message: types.Message, state: FSMContext):
    if message.text == "Back":
        await message.answer(
            "Would you like to provide an additional phone number? If yes, please share it. If not, reply with 'No'.",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text="Back")]],
                resize_keyboard=True
            )
        )
        await state.set_state(Registration.waiting_for_additional_phone)
        return

    try:
        dob = datetime.strptime(message.text, "%d/%m/%Y")
        if dob.year < 1950 or dob > datetime.now():
            raise ValueError
        current_year = datetime.now().year
        age = current_year - dob.year
        age_category = f"{(age // 10) * 10}-{(age // 10) * 10 + 9}"
        await state.update_data(dob=message.text, age_category=age_category)

        # Show region selection buttons
        regions = [
            "Andijan", "Bukhara", "Fergana", "Jizzakh", "Kashkadarya",
            "Karakalpakstan Republic", "Namangan", "Navoi", "Samarkand",
            "Sirdarya", "Surkhandarya", "Tashkent Region", "Tashkent City",
            "Khorezm", "Other"
        ]
        region_kb = ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text=region)] for region in regions],
            resize_keyboard=True
        )
        await message.answer("Please select your region:", reply_markup=region_kb)
        await state.set_state(Registration.waiting_for_region)
    except ValueError:
        await message.answer(
            "Invalid date. Please provide your date of birth in the format DD/MM/YYYY and ensure the year is valid.",
            reply_markup=back_keyboard()
        )

@router.message(Registration.waiting_for_region)
async def process_region(message: types.Message, state: FSMContext):
    regions = [
        "Andijan", "Bukhara", "Fergana", "Jizzakh", "Kashkadarya",
        "Karakalpakstan Republic", "Namangan", "Navoi", "Samarkand",
        "Sirdarya", "Surkhandarya", "Tashkent Region", "Tashkent City",
        "Khorezm", "Other"
    ]

    data = await state.get_data()

    if message.text == "Back":
        await message.answer("Please provide your date of birth (DD/MM/YYYY):", reply_markup=back_keyboard())
        await state.set_state(Registration.waiting_for_dob)
        return

    if data.get("awaiting_custom_region"):
        await state.update_data(region=message.text)
        await state.update_data(awaiting_custom_region=False)

        # Next step
        study_kb = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="Active")],
                [KeyboardButton(text="Passive")],
                [KeyboardButton(text="Back")]
            ],
            resize_keyboard=True
        )
        await message.answer(
            """Which mode of studying do you want to choose?

✨ *ACTIVE*
_What do you get?_
• Live lectures & HW materials _forever_
• 🎮 Playing games in group
• 🏆 Prizes, like 'IELTS free sit', or 'Free courses for a friend'
• 📊 Monitoring of your HW submission

⚠️ _Important:_ If you do not send your HW 5 times, you will be expelled from the course!

💰 *PASSIVE*
_What do you get?_
• Live lectures & HW materials _forever_
• 🚫 No prizes
• 🚫 No games
• ❌ HW submission will not be monitored
• 👍 You will never be expelled from the course""",
            parse_mode="Markdown",
            reply_markup=study_kb
        )
        await state.set_state(Registration.waiting_for_mode_of_study)
        return

    if message.text in regions:
        if message.text == "Other":
            await state.update_data(awaiting_custom_region=True)
            await message.answer("Please type the name of your region:")
            return
        else:
            await state.update_data(region=message.text)
            # Next step
            study_kb = ReplyKeyboardMarkup(
                keyboard=[
                    [KeyboardButton(text="Active")],
                    [KeyboardButton(text="Passive")],
                    [KeyboardButton(text="Back")]
                ],
                resize_keyboard=True
            )
            await message.answer(
                """Which mode of studying do you want to choose?

✨ *ACTIVE*
_What do you get?_
• Live lectures & HW materials _forever_
• 🎮 Playing games in group
• 🏆 Prizes, like 'IELTS free sit', or 'Free courses for a friend'
• 📊 Monitoring of your HW submission

⚠️ _Important:_ If you do not send your HW 5 times, you will be expelled from the course!

💰 *PASSIVE*
_What do you get?_
• Live lectures & HW materials _forever_
• 🚫 No prizes
• 🚫 No games
• ❌ HW submission will not be monitored
• 👍 You will never be expelled from the course""",
                parse_mode="Markdown",
                reply_markup=study_kb
            )
            await state.set_state(Registration.waiting_for_mode_of_study)
            return

    else:
        region_kb = ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text=region)] for region in regions],
            resize_keyboard=True
        )
        await message.answer("Invalid selection. Please select your region from the buttons below:", reply_markup=region_kb)

@router.message(Registration.waiting_for_mode_of_study)
async def process_study_mode(message: types.Message, state: FSMContext):
    if message.text == "Back":
        regions = [
            "Andijan", "Bukhara", "Fergana", "Jizzakh", "Kashkadarya",
            "Karakalpakstan Republic", "Namangan", "Navoi", "Samarkand",
            "Sirdarya", "Surkhandarya", "Tashkent Region", "Tashkent City",
            "Khorezm", "Other"
        ]
        region_kb = ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text=region)] for region in regions],
            resize_keyboard=True
        )
        await message.answer("Please select your region:", reply_markup=region_kb)
        await state.set_state(Registration.waiting_for_region)
        return

    if message.text not in ["Active", "Passive"]:
        await message.answer("Invalid choice. Please select either Active or Passive using the buttons.")
        return

    await state.update_data(mode_of_study=message.text)

    if message.text == "Passive":
        referral_kb = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="Previous courses")],
                [KeyboardButton(text="Telegram Advertisement")],
                [KeyboardButton(text="Recommended by friend")],
                [KeyboardButton(text="Instagram Advertisement")],
                [KeyboardButton(text="YouTube Videos")],
                [KeyboardButton(text="Back")],
            ],
            resize_keyboard=True,
        )
        await message.answer("You chose the PASSIVE mode! Moving to the next step. How did you hear about us?", reply_markup=referral_kb)
        await state.set_state(Registration.waiting_for_referral)
    else:
        hw_kb = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="6 times per week")],
                [KeyboardButton(text="Back")],
            ],
            resize_keyboard=True,
        )
        await message.answer(
            """🎉 *Hurrah!* You chose the *ACTIVE* mode!!!

📅 *How many times a week do you want to do HW?*
We understand that everyone has a different lifestyle, so please choose a plan that suits you best 😉""",
            parse_mode="Markdown",
            reply_markup=hw_kb
        )
        await state.set_state(Registration.waiting_for_hw_frequency)

@router.message(Registration.waiting_for_hw_frequency)
async def process_hw_frequency(message: types.Message, state: FSMContext):
    if message.text == "Back":
        study_kb = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="Active")],
                [KeyboardButton(text="Passive")],
                [KeyboardButton(text="Back")],
            ],
            resize_keyboard=True,
        )
        await message.answer("Please select your mode of study:", reply_markup=study_kb)
        await state.set_state(Registration.waiting_for_mode_of_study)
        return

    if message.text not in ["6 times per week"]:
        await message.answer("Invalid choice. Please select one of the HW Frequency options or press /start to restart.")
        return

    await state.update_data(hw_frequency=message.text)

    referral_kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Previous courses")],
            [KeyboardButton(text="Telegram Advertisement")],
            [KeyboardButton(text="Recommended by friend")],
            [KeyboardButton(text="Instagram Advertisement")],
            [KeyboardButton(text="YouTube Videos")],
            [KeyboardButton(text="Back")],
        ],
        resize_keyboard=True,
    )
    await message.answer("How did you hear about us?", reply_markup=referral_kb)
    await state.set_state(Registration.waiting_for_referral)

@router.message(Registration.waiting_for_referral)
async def process_referral(message: types.Message, state: FSMContext):
    if message.text == "Back":
        user_data = await state.get_data()
        if user_data.get("mode_of_study") == "Passive":
            study_kb = ReplyKeyboardMarkup(
                keyboard=[
                    [KeyboardButton(text="Active")],
                    [KeyboardButton(text="Passive")],
                    [KeyboardButton(text="Back")],
                ],
                resize_keyboard=True,
            )
            await message.answer("Please select your mode of study:", reply_markup=study_kb)
            await state.set_state(Registration.waiting_for_mode_of_study)
        else:
            hw_kb = ReplyKeyboardMarkup(
                keyboard=[
                    [KeyboardButton(text="6 times per week")],
                    [KeyboardButton(text="Back")],
                ],
                resize_keyboard=True,
            )
            await message.answer("Please select your HW Frequency:", reply_markup=hw_kb)
            await state.set_state(Registration.waiting_for_hw_frequency)
        return

    await state.update_data(referral_source=message.text)
    user_data = await state.get_data()
    username = message.from_user.username if message.from_user.username else "Not Provided"
    unique_id = generate_unique_id(sheet)
    tz_tashkent = pytz.timezone("Asia/Tashkent")
    registration_time = datetime.now(tz_tashkent).strftime("%d/%m/%Y %H:%M:%S")
    await message.answer("Thank you for registering! 🎉\n\n")

    new_row = [
        user_data['name'],
        user_data['phone'],
        user_data.get('additional_phone', 'N/A'),
        username,
        user_data['dob'],
        user_data['age_category'],
        user_data['region'],
        user_data['mode_of_study'],
        user_data.get('hw_frequency', 'N/A'),
        user_data['referral_source'],
        unique_id,
        message.from_user.id,
        registration_time
    ]

    try:
        sheet.append_row(new_row, value_input_option="RAW")
        await message.answer(
            f"✨ *Your Unique ID:* {unique_id}\n",
            parse_mode="Markdown"
        )
        await message.answer("Welcome to the main menu! Please choose an option below:",
                             reply_markup=main_menu_keyboard())
        await state.clear()
    except Exception as e:
        logging.error(f"Error writing to Google Sheets: {e}")
        await message.answer(f"An error occurred while saving your data: {e}")


################################################################################
# 5) Profile & Editing Handlers
################################################################################

def find_row_by_telegram_id(sheet, telegram_id):
    rows = sheet.get_all_values()
    headers = rows[0]
    telegram_id_index = headers.index("Telegram ID")
    for i, row in enumerate(rows[1:], start=2):
        if len(row) > telegram_id_index and row[telegram_id_index] == str(telegram_id):
            return i, row
    return None, None

def update_google_sheets(sheet, telegram_id, field, value):
    row_index, _ = find_row_by_telegram_id(sheet, telegram_id)
    if row_index:
        headers = sheet.row_values(1)
        col_index = headers.index(field) + 1
        sheet.update_cell(row_index, col_index, value)
        return True
    return False

@router.message(Command(commands=["edit"]))
async def cmd_edit(message: types.Message, state: FSMContext):
    if not is_user_registered(sheet, message.from_user.id):
        await message.answer("You are not registered yet. Use /start to register.")
        return

    edit_kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Edit Full Name")],
            [KeyboardButton(text="Edit Phone Number")],
            [KeyboardButton(text="Edit Additional Phone Number")],
            [KeyboardButton(text="Edit Date of Birth")],
            [KeyboardButton(text="Edit Region")],
            [KeyboardButton(text="Edit HW Frequency")],
            [KeyboardButton(text="Back")],
        ],
        resize_keyboard=True
    )
    await message.answer("What information would you like to edit?", reply_markup=edit_kb)
    await state.set_state(Registration.editing_information)

@router.message(Registration.editing_information)
async def edit_info_handler(message: types.Message, state: FSMContext):
    options = {
        "Edit Full Name": "Full Name",
        "Edit Phone Number": "Telephone Number",
        "Edit Additional Phone Number": "Additional Telephone Number",
        "Edit Date of Birth": "Date of Birth",
        "Edit Region": "Region",
        "Edit HW Frequency": "HW Frequency",
    }

    if message.text == "Back":
        await message.answer("Returning to the main menu.", reply_markup=main_menu_keyboard())
        await state.clear()
        return

    user_row_index, user_row = find_row_by_telegram_id(sheet, message.from_user.id)
    if not user_row:
        await message.answer("Profile not found. Please register using /start.")
        return

    field = options.get(message.text)
    if not field:
        await message.answer("Invalid option. Please select a valid button.")
        return

    # HW Frequency only if Active
    if field == "HW Frequency":
        study_mode_index = sheet.row_values(1).index("Study Mode")
        if user_row[study_mode_index] != "Active":
            await message.answer("HW Frequency can only be edited for Active study mode.")
            return
        hw_kb = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="6 times per week")],
                [KeyboardButton(text="Back")],
            ],
            resize_keyboard=True,
        )
        await message.answer("Please select the new HW Frequency:", reply_markup=hw_kb)
        await state.set_state(Registration.editing_field)
        await state.update_data(editing_field="HW Frequency")
        return

    # Prompt for new data
    if field == "Date of Birth":
        await message.answer("Please provide your new Date of Birth in the format DD/MM/YYYY:")
    elif field == "Region":
        regions = [
            "Andijan", "Bukhara", "Fergana", "Jizzakh", "Kashkadarya",
            "Karakalpakstan Republic", "Namangan", "Navoi", "Samarkand",
            "Sirdarya", "Surkhandarya", "Tashkent Region", "Tashkent City",
            "Khorezm", "Other",
        ]
        region_kb = ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text=region)] for region in regions],
            resize_keyboard=True,
        )
        await message.answer("Please select your new Region:", reply_markup=region_kb)
    else:
        await message.answer(f"Please provide your new {field.lower()}:")
    await state.update_data(editing_field=field)
    await state.set_state(Registration.editing_field)

@router.message(Registration.editing_field)
async def save_edited_data(message: types.Message, state: FSMContext):
    data = await state.get_data()
    editing_field = data.get("editing_field")

    if not editing_field:
        await message.answer("Unexpected input. Please use /edit to restart.")
        await state.clear()
        return

    # Handle phone
    if editing_field == "Telephone Number":
        if message.text == "Back":
            await message.answer("Returning to the main menu.", reply_markup=main_menu_keyboard())
            await state.clear()
            return
        if message.contact:
            phone_number = message.contact.phone_number
        else:
            phone_number = message.text
            if not phone_number.isdigit() or len(phone_number) < 10:
                phone_kb = ReplyKeyboardMarkup(
                    keyboard=[
                        [KeyboardButton(text="Share Phone Number", request_contact=True)],
                        [KeyboardButton(text="Back")],
                    ],
                    resize_keyboard=True,
                )
                await message.answer(
                    "Invalid phone number. Please use the 'Share Phone Number' button.",
                    reply_markup=phone_kb
                )
                return
        if update_google_sheets(sheet, message.from_user.id, "Telephone Number", phone_number):
            await message.answer("Your telephone number has been updated successfully.")
        else:
            await message.answer("Failed to update your telephone number. Please try again.")
        await state.clear()
        await message.answer("Returning to the main menu.", reply_markup=main_menu_keyboard())
        return

    # Validate DOB
    if editing_field == "Date of Birth":
        try:
            dob = datetime.strptime(message.text, "%d/%m/%Y")
            if dob.year < 1950 or dob > datetime.now():
                raise ValueError
        except ValueError:
            await message.answer("Invalid date. Please use DD/MM/YYYY format.")
            return

    # Validate Full Name
    if editing_field == "Full Name":
        name_parts = message.text.split()
        if len(name_parts) < 2 or not all(part.replace("'", "").isalpha() for part in name_parts):
            await message.answer(
                "Invalid name. Please provide your full name (e.g., John Doe). Ensure it contains at least two words and only letters or the ' symbol.",
            )
            return

    # Validate HW Frequency
    if editing_field == "HW Frequency" and message.text not in ["6 times per week"]:
        await message.answer("Invalid choice. Please select a valid HW Frequency.")
        return

    # Region "Other"
    if editing_field == "Region" and message.text == "Other":
        await message.answer("Please type the name of your region:")
        await state.update_data(awaiting_custom_region=True)
        return

    if data.get("awaiting_custom_region"):
        await state.update_data(region=message.text)
        await state.update_data(awaiting_custom_region=False)
        editing_field = "Region"

    # Update sheet
    if update_google_sheets(sheet, message.from_user.id, editing_field, message.text):
        await message.answer(f"Your {editing_field.lower()} has been updated successfully.")
    else:
        await message.answer(f"Failed to update your {editing_field.lower()}. Please try again.")

    await state.clear()
    await message.answer("Returning to the main menu.", reply_markup=main_menu_keyboard())

async def show_profile(message: types.Message):
    logging.info("Executing show_profile function")
    telegram_id = str(message.from_user.id)
    try:
        rows = sheet.get_all_values()
        headers = [header.strip() for header in rows[0]]
        logging.info(f"Fetched headers from Google Sheets: {headers}")

        required_columns = [
            "Full Name",
            "Telephone Number",
            "Telegram ID",
            "Additional Telephone Number",
            "Date of Birth",
            "Region",
            "Study Mode",
            "HW Frequency",
            "Unique ID"
        ]
        for column in required_columns:
            if column not in headers:
                raise ValueError(f"Column '{column}' is missing in Google Sheets headers: {headers}")

        telegram_id_col_index = headers.index("Telegram ID")
        for row in rows[1:]:
            if len(row) > telegram_id_col_index and row[telegram_id_col_index].strip() == telegram_id:
                profile_info = (
                    f"👤 *Your Profile:*\n"
                    f"*🆔 Your ID: {row[headers.index('Unique ID')].strip()} *\n"
                    f"- *Full Name:* {row[headers.index('Full Name')].strip()}\n"
                    f"- *Telephone Number:* {row[headers.index('Telephone Number')].strip()}\n"
                    f"- *Additional Telephone Number:* {row[headers.index('Additional Telephone Number')].strip()}\n"
                    f"- *Date of Birth:* {row[headers.index('Date of Birth')].strip()}\n"
                    f"- *Region:* {row[headers.index('Region')].strip()}\n"
                    f"- *Study Mode:* {row[headers.index('Study Mode')].strip()}\n"
                    f"- *HW Frequency:* {row[headers.index('HW Frequency')].strip()}\n"
                    f"\n\n*To change data, send* /edit"
                )
                await message.answer(profile_info, parse_mode="Markdown")
                return

        await message.answer("Profile not found. Please register using /start.")
    except ValueError as ve:
        logging.error(f"ValueError: {ve}")
        await message.answer("An error occurred while fetching your profile. Missing column in Google Sheets.")
    except Exception as e:
        logging.error("Unexpected Error:", exc_info=e)
        await message.answer("An unexpected error occurred while fetching your profile.")

def main_menu_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Profile")],
            [KeyboardButton(text="Homework")],
            [KeyboardButton(text="My points")],
            [KeyboardButton(text="Top List")],
            [KeyboardButton(text="Contact Admin")],
        ],
        resize_keyboard=True
    )

@router.message(Command(commands=["profile"]))
async def profile_command_handler(message: types.Message):
    logging.info("Profile command handler triggered")
    await show_profile(message)


################################################################################
# 6) Homework Submission FSM & Handlers
################################################################################

class HomeworkSubmission(StatesGroup):
    waiting_for_homework_selection = State()
    waiting_for_homework_submission = State()

def get_student_fullname(telegram_id):
    try:
        rows = sheet.get_all_values()
        headers = rows[0]
        if "Full Name" not in headers or "Telegram ID" not in headers:
            return None
        telegram_id_idx = headers.index("Telegram ID")
        full_name_idx = headers.index("Full Name")
        for row in rows[1:]:
            if len(row) > telegram_id_idx and row[telegram_id_idx].strip() == str(telegram_id):
                return row[full_name_idx].strip()
    except Exception as e:
        logging.error(f"Error retrieving full name: {e}")
    return None

@router.message(Command(commands=["homework"]))
async def homework_command_handler(message: types.Message, state: FSMContext):
    logging.info("Homework command handler triggered")

    student_data = sheet.get_all_records()
    telegram_id_str = str(message.from_user.id)
    student_info = next((row for row in student_data if str(row.get("Telegram ID", "")).strip() == telegram_id_str), None)

    if not student_info:
        await message.answer("Your information was not found. Please register using /start.")
        return

    unique_id = student_info.get("Unique ID")
    group_number = student_info.get("GROUP NUMBER")
    if not group_number:
        await message.answer("Your group number is missing in our records. Please contact support.")
        return

    group_sheet_name = f"G#{group_number}"
    try:
        group_sheet = client.open_by_key("REPLACE_SHEET2_ID").worksheet(group_sheet_name)
    except gspread.exceptions.WorksheetNotFound:
        await message.answer(f"Group sheet '{group_sheet_name}' not found.")
        return

    raw_data = group_sheet.get_all_values()
    if len(raw_data) < 5:
        await message.answer("Homework data is not available at the moment.")
        return

    # row 3 => HW headers ( "1", "2", "3", ... up to "30" )
    # row 4 => deadlines
    # row 5 => teacher answers
    headers = raw_data[2]   # row 3
    row_deadlines = raw_data[3]  # row 4
    row_answers   = raw_data[4]  # row 5
    data_rows = raw_data[5:]     # student data from row 6 onward

    # Find student's row by unique ID
    student_row_number = None
    student_row = None
    for idx, row in enumerate(data_rows, start=6):
        if len(row) > 0 and row[0].strip() == unique_id:
            student_row_number = idx
            student_row = row
            break

    if not student_row:
        await message.answer("Your homework record was not found in the group sheet.")
        return

    # Incomplete if teacher has set both deadline & answers, but student's cell is "" or "0"
    missing_homeworks = []
    for hw_num in range(1, 31):
        hw_str = str(hw_num)
        if hw_str in headers:
            col_index = headers.index(hw_str)

            deadline_val = row_deadlines[col_index].strip() if col_index < len(row_deadlines) else ""
            answers_val  = row_answers[col_index].strip() if col_index < len(row_answers) else ""

            if (deadline_val != "") and (answers_val != ""):
                student_cell_val = student_row[col_index] if col_index < len(student_row) else ""
                if student_cell_val.strip() == "" or student_cell_val.strip() == "0":
                    missing_homeworks.append(hw_num)

    if not missing_homeworks:
        await message.answer("👏 Congratulations! You have submitted all available homeworks.")
        return

    hw_buttons = [[KeyboardButton(text=f"#{num}")] for num in missing_homeworks]
    hw_kb = ReplyKeyboardMarkup(keyboard=hw_buttons, resize_keyboard=True)

    await message.answer(
        "Select which homework to submit (shown only if teacher set both deadline & answers, and you haven't submitted yet):",
        reply_markup=hw_kb
    )
    await state.update_data(
        group_sheet_key="REPLACE_SHEET2_ID",
        group_sheet_name=group_sheet_name,
        student_row_number=student_row_number,
        unique_id=unique_id,
        homework_headers=headers
    )
    await state.set_state(HomeworkSubmission.waiting_for_homework_selection)

@router.message(HomeworkSubmission.waiting_for_homework_selection)
async def homework_selection_handler(message: types.Message, state: FSMContext):
    text = message.text.strip()
    if text.lower() == "back":
        await state.clear()
        await message.answer("Returning to the main menu.", reply_markup=main_menu_keyboard())
        return

    if not text.startswith("#"):
        await message.answer("Please select a valid homework button (e.g., #15).")
        return
    try:
        selected_hw = int(text[1:])
    except ValueError:
        await message.answer("Invalid homework number.")
        return

    await state.update_data(selected_homework=selected_hw)

    homework_instructions = (
        f"You selected homework #{selected_hw}.\n\n"
        "Please send your homework in the following format:\n"
        "1. a\n"
        "2. b\n"
        "3. c\n"
        "...\n\n"
        "After submitting:\n"
        "• We'll compare it to the teacher's official answers.\n"
        "• If at least 30% overlap, it's considered correct, otherwise you'll be asked to re-submit.\n\n"
        "Points:\n"
        "• 15 points if on/before deadline.\n"
        "• 10 points if late.\n"
        "If no deadline is set, full mark (15)."
    )
    # Provide a Back button or /menu button at this stage
    back_or_menu_kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Back")],
            [KeyboardButton(text="/menu")],
        ],
        resize_keyboard=True
    )
    await message.answer(homework_instructions, reply_markup=back_or_menu_kb)
    await state.set_state(HomeworkSubmission.waiting_for_homework_submission)

@router.message(HomeworkSubmission.waiting_for_homework_submission)
async def process_homework_submission(message: types.Message, state: FSMContext):
    if message.text.strip().lower() == "back":
        await homework_command_handler(message, state)
        return
    if message.text.strip().lower() == "/menu":
        await state.clear()
        await message.answer("Returning to main menu.", reply_markup=main_menu_keyboard())
        return

    data = await state.get_data()
    unique_id = data.get("unique_id")
    group_sheet_key = data.get("group_sheet_key")
    group_sheet_name = data.get("group_sheet_name")
    selected_hw = data.get("selected_homework")
    student_row_number = data.get("student_row_number")
    homework_headers = data.get("homework_headers")

    if not all([unique_id, group_sheet_key, group_sheet_name, selected_hw, student_row_number, homework_headers]):
        await message.answer("Some required data is missing. Please try again.")
        await state.clear()
        return

    try:
        group_sheet = client.open_by_key(group_sheet_key).worksheet(group_sheet_name)
    except gspread.exceptions.WorksheetNotFound:
        await message.answer(f"Group sheet '{group_sheet_name}' not found.")
        await state.clear()
        return

    hw_header_str = str(selected_hw)
    if hw_header_str not in homework_headers:
        await message.answer("Homework column not found. Please contact admin.")
        await state.clear()
        return
    col_index = homework_headers.index(hw_header_str) + 1

    # Teacher’s RAW answers from row=5, col=(4 + HW#)
    teacher_answers_raw = ""
    try:
        teacher_answers_raw = group_sheet.cell(5, 4 + selected_hw).value or ""
    except Exception:
        teacher_answers_raw = ""

    teacher_parsed = parse_text(teacher_answers_raw)
    student_parsed = parse_text(message.text)

    # Overall similarity check
    if teacher_parsed:  
        similarity = calculate_similarity(student_parsed, teacher_parsed)
        if similarity < 0.30:
            # CHANGE #1: Use the "menu_only_keyboard" so no "Re-submit" is shown
            await message.answer(
                "Your answers do not match enough of the teacher's answers. More than 70% of your answer is wrong.\n"
                "Please re-send your homework in the required format:\n"
                "1. a\n"
                "2. b\n"
                "3. c\n"
                "...\n\n",
                reply_markup=menu_only_keyboard()
            )
            return

    # Calculate score by deadline
    deadline_cell = group_sheet.cell(4, 4 + selected_hw).value or ""
    score = "15"
    if deadline_cell.strip():
        try:
            deadline_dt = datetime.strptime(deadline_cell.strip(), "%Y.%m.%d, %H:%M")
            deadline_dt = pytz.timezone("Asia/Tashkent").localize(deadline_dt)
            now = datetime.now(pytz.timezone("Asia/Tashkent"))
            if now > deadline_dt:
                score = "10"
        except Exception as e:
            logging.error(f"Error parsing deadline: {e}")

    # Update student's cell
    try:
        group_sheet.update_cell(student_row_number, col_index, score)
    except Exception as e:
        logging.error(f"Error updating homework submission: {e}")
        await message.answer(f"An error occurred while submitting your homework: {e}")
        await state.clear()
        return

    # Forward submission
    full_name = get_student_fullname(message.from_user.id) or "Not Provided"
    similarity_str = (
        f"{round(calculate_similarity(student_parsed, teacher_parsed)*100,1)}%"
        if teacher_parsed else "N/A"
    )
    forward_text = (
        f"📥 *New Homework Submission!*\n\n"
        f"*Student Name:* {full_name}\n"
        f"*Telegram ID:* {message.from_user.id}\n"
        f"*Homework Number:* #{selected_hw}\n"
        f"*Similarity:* {similarity_str}\n\n"
        f"*Submitted Content:*\n{message.text}"
    )
    try:
        await bot.send_message(GROUP_CHAT_ID, forward_text, parse_mode="Markdown")
    except Exception as e:
        logging.error(f"Error forwarding submission to group: {e}")

    # Generate line-by-line report
    line_report = generate_line_by_line_report(teacher_answers_raw, message.text)

    # Finally, send teacher answers to the student + line-by-line result
    if teacher_answers_raw:
        await message.answer(
            f"✅ Homework #{selected_hw} submitted successfully! Your grade is {score} points.\n\n"
            f"**Here are the teacher's official answers:**\n{teacher_answers_raw}\n\n"
            f"**Your Line-by-Line Results:**\n{line_report}",
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard()
        )
    else:
        await message.answer(
            f"✅ Homework #{selected_hw} submitted successfully! Your grade is {score} points.\n\n"
            f"(No official answers were set by the teacher.)\n\n"
            f"**Your Line-by-Line Results:**\n{line_report}",
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard()
        )

    await state.clear()


################################################################################
# 7) Deadline Setting FSM (Admins Only)
################################################################################

class DeadlineFSM(StatesGroup):
    waiting_for_deadline_selection = State()
    waiting_for_deadline_input = State()
    waiting_for_answers_input = State()

@router.message(Command(commands=["deadline"]))
async def deadline_command_handler(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("You are not authorized to use this command. Only admins can set deadlines.")
        return

    worksheets_to_check = ["G#1", "G#2", "G#3", "G#4"]
    free_deadline_options = []

    for ws_name in worksheets_to_check:
        try:
            ws = client.open_by_key("REPLACE_SHEET2_ID").worksheet(ws_name)
        except gspread.exceptions.WorksheetNotFound:
            continue

        row4 = ws.row_values(4)
        if len(row4) < 34:
            row4 += [""] * (34 - len(row4))

        for hw_num in range(1, 31):
            col_index = 4 + hw_num
            if row4[col_index - 1].strip() == "":
                free_deadline_options.append(f"{ws_name} - #{hw_num}")

    if not free_deadline_options:
        await message.answer("All deadlines have been set for these groups.")
        return

    buttons = [[KeyboardButton(text=option)] for option in free_deadline_options]
    kb = ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)
    await message.answer("Select the worksheet and homework for which to set the deadline:", reply_markup=kb)
    await state.set_state(DeadlineFSM.waiting_for_deadline_selection)

@router.message(DeadlineFSM.waiting_for_deadline_selection)
async def deadline_selection_handler(message: types.Message, state: FSMContext):
    text = message.text.strip()
    if " - " not in text or not text.startswith("G#"):
        await message.answer("Please select a valid option (e.g., G#1 - #5).")
        return
    try:
        sheet_name, hw_part = text.split(" - ")
        if not hw_part.startswith("#"):
            raise ValueError
        selected_hw = int(hw_part[1:])
    except Exception:
        await message.answer("Invalid format. Please try again (e.g., G#1 - #5).")
        return

    await state.update_data(selected_deadline_ws=sheet_name, selected_deadline_hw=selected_hw)
    await message.answer(
        f"Please send deadline for {sheet_name} homework #{selected_hw} in this format (YYYY.MM.DD, HH:MM):",
        reply_markup=back_keyboard()
    )
    await state.set_state(DeadlineFSM.waiting_for_deadline_input)

@router.message(DeadlineFSM.waiting_for_deadline_input)
async def deadline_input_handler(message: types.Message, state: FSMContext):
    if message.text.strip().lower() == "back":
        await state.clear()
        await message.answer("Returning to main menu.", reply_markup=main_menu_keyboard())
        return

    data = await state.get_data()
    selected_ws = data.get("selected_deadline_ws")
    selected_hw = data.get("selected_deadline_hw")
    try:
        _ = datetime.strptime(message.text.strip(), "%Y.%m.%d, %H:%M")
    except Exception:
        await message.answer("Invalid format. Please send deadline in the format (YYYY.MM.DD, HH:MM).")
        return

    try:
        ws = client.open_by_key("REPLACE_SHEET2_ID").worksheet(selected_ws)
    except gspread.exceptions.WorksheetNotFound:
        await message.answer(f"Worksheet {selected_ws} not found.")
        await state.clear()
        return

    col_index = 4 + selected_hw
    try:
        ws.update_cell(4, col_index, message.text.strip())
        await state.update_data(deadline_confirmed=True)
        await message.answer(
            f"Deadline for {selected_ws} homework #{selected_hw} has been set to {message.text.strip()}.\n\n"
            "Now please send the official **answers** for this homework in the format:\n"
            "`1. a\n2. b\n3. word`\n(They will be cleaned for similarity check, but stored in the sheet as-is.)",
            parse_mode="Markdown"
        )
        await state.set_state(DeadlineFSM.waiting_for_answers_input)
    except Exception as e:
        await message.answer(f"An error occurred while setting the deadline: {e}")
        await state.clear()

@router.message(DeadlineFSM.waiting_for_answers_input)
async def teacher_answers_input_handler(message: types.Message, state: FSMContext):
    if message.text.strip().lower() == "back":
        await state.clear()
        await message.answer("Returning to main menu.", reply_markup=main_menu_keyboard())
        return

    data = await state.get_data()
    selected_ws = data.get("selected_deadline_ws")
    selected_hw = data.get("selected_deadline_hw")

    teacher_raw_text = message.text.strip()
    teacher_parsed = parse_text(teacher_raw_text)

    try:
        ws = client.open_by_key("REPLACE_SHEET2_ID").worksheet(selected_ws)
        ws.update_cell(5, 4 + selected_hw, teacher_raw_text)

        await message.answer(
            f"Official answers for {selected_ws} homework #{selected_hw} are saved.\n\n"
            f"<b>Raw teacher answers:</b>\n{teacher_raw_text}\n\n"
            f"<b>(Parsed for similarity check):</b>\n{teacher_parsed}",
            parse_mode="HTML",
            reply_markup=main_menu_keyboard()
        )
    except Exception as e:
        await message.answer(f"An error occurred while saving teacher answers: {e}")

    await state.clear()


################################################################################
# 8) Other Commands & Features
################################################################################

@router.message(Command(commands=["contactAdmin"]))
async def contact_admin_command_handler(message: types.Message):
    logging.info("Contact Admin command handler triggered")
    admin_contact = (
        "📞 *Contact Admin:*\n"
        "- Name: John Doe\n"
        "- Phone: +1234567890\n"
        "- Telegram: @AdminUsername\n"
    )
    await message.answer(admin_contact, parse_mode="Markdown")

async def my_points(message: types.Message):
    try:
        telegram_id = str(message.from_user.id)
        student_data = sheet.get_all_records()
        student_info = next((row for row in student_data if str(row["Telegram ID"]) == telegram_id), None)

        if not student_info:
            await message.answer("⚠️ Your information was not found in the database.")
            return

        unique_id = student_info["Unique ID"]
        group_number = student_info.get("GROUP NUMBER")
        if not group_number or not isinstance(group_number, int):
            await message.answer("⚠️ Your group number is missing or invalid in the database.")
            return

        group_sheet_name = f"G#{group_number}"
        try:
            group_sheet = client.open_by_key("REPLACE_SHEET2_ID").worksheet(group_sheet_name)
        except gspread.exceptions.WorksheetNotFound:
            await message.answer(f"⚠️ Group sheet '{group_sheet_name}' not found.")
            return

        raw_data = group_sheet.get_all_values()
        headers = raw_data[2]
        rows = raw_data[3:]

        filtered_headers = headers[:34]
        filtered_rows = [row[:34] for row in rows]

        student_data_list = [
            dict(zip(filtered_headers, row)) for row in filtered_rows if len(row) == len(filtered_headers)
        ]
        logging.info(f"Student Data List: {student_data_list}")

        student_scores = next((row for row in student_data_list if row.get('') == unique_id), None)
        logging.info(f"Student Scores: {student_scores}")

        if not student_scores:
            await message.answer("⚠️ No scores were found for your account in the group sheet.")
            return

        scores_table = "📊 **Your Scores:**\n\n"
        for day in range(1, 31):
            day_column = f"{day}"
            score = student_scores.get(day_column, "0")
            scores_table += f"DAY{day:3} | {score}\n"

        await message.answer(scores_table, parse_mode="Markdown")
    except Exception as e:
        logging.error(f"Error in 'my_points': {e}")
        await message.answer("⚠️ An error occurred while fetching your points. Please try again later.")

def get_top_list():
    try:
        data = sheet2.get_all_values()
        if len(data) > 1:
            header = data[1]
            entries = data[2:]

            valid_entries = []
            missing_entries = []

            for row in entries:
                group_number = row[1] if row[1] and row[1] != "#REF!" else "Not Found"
                score = row[2] if row[2] and row[2] != "#REF!" else "❌ Data Missing"

                if group_number != "Not Found" and score != "❌ Data Missing":
                    valid_entries.append((group_number, score))
                else:
                    missing_entries.append((group_number, score))

            top_list = "🏆 <b>Top List</b>\n"
            top_list += "<pre>"
            top_list += "{:<3} {:<15} {:<10}\n".format("", "Group Number", "Score")
            top_list += "-" * 30 + "\n"

            idx = 1
            for group, score in valid_entries:
                top_list += "{:<3} {:<15} {:<10}\n".format(idx, group, score)
                idx += 1

            for group, score in missing_entries:
                top_list += "{:<3} {:<15} {:<10}\n".format(idx, group, score)
                idx += 1

            top_list += "</pre>"
            return top_list
        else:
            return "No data available in the sheet."
    except Exception as e:
        return f"Error fetching top list: {e}"

@router.message(Command(commands=['toplist']))
async def send_top_list(message: types.Message):
    await message.answer(get_top_list(), parse_mode="HTML")

@router.message(Command(commands=["menu"]))
async def menu_command_handler(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Welcome to the main menu! Please choose an option below:", reply_markup=main_menu_keyboard())

@router.message(lambda message: message.text and message.text.lower() == "my points")
async def my_points_button_handler(message: types.Message):
    logging.info("My points button handler triggered")
    await my_points(message)

@router.message(lambda message: message.text and message.text.lower() == "profile")
async def profile_button_handler(message: types.Message):
    logging.info("Profile button handler triggered")
    await show_profile(message)

@router.message(lambda message: message.text and message.text.lower() == "top list")
async def top_list_button_handler(message: types.Message):
    logging.info("Top List button handler triggered")
    await message.answer(get_top_list(), parse_mode="HTML")

@router.message(lambda message: message.text and message.text.lower() == "homework")
async def homework_button_handler(message: types.Message, state: FSMContext):
    logging.info("Homework button handler triggered")
    await homework_command_handler(message, state)

@router.message(lambda message: message.text and message.text.lower() == "contact admin")
async def contact_admin_button_handler(message: types.Message):
    logging.info("Contact Admin button handler triggered")
    admin_contact = (
        "📞 *Contact Admin:*\n"
        "- Name: John Doe\n"
        "- Phone: +1234567890\n"
        "- Telegram: @AdminUsername\n"
    )
    await message.answer(admin_contact, parse_mode="Markdown")

@router.message(lambda message: message.text and not message.text.startswith("/")
                and message.text.lower() not in ["profile", "homework", "contact admin", "my points", "top list"])
async def fallback_handler(message: types.Message):
    await message.answer("I didn't understand that. Please use the menu options or send a valid command.")


################################################################################
# 9) Admin Broadcasting Command
################################################################################

@router.message(Command(commands=["message"]))
async def admin_message_handler(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("You are not authorized to use this command.")
        return

    try:
        content = message.caption or message.text
        if not content or "{" not in content or "}" not in content:
            raise ValueError("Invalid format. Use: /message [Caption or Text] {ALL | IDs}")

        command_parts = content.split("{", 1)
        msg_content = command_parts[0].replace("/message", "").strip()
        targets = command_parts[1].split("}")[0].strip()

        media_type = None
        media_file_id = None
        if message.photo:
            media_type = "photo"
            media_file_id = message.photo[-1].file_id
        elif message.video:
            media_type = "video"
            media_file_id = message.video.file_id
        elif message.audio:
            media_type = "audio"
            media_file_id = message.audio.file_id
        elif message.document:
            media_type = "document"
            media_file_id = message.document.file_id

        if targets.lower() == "all":
            # Send to all registered users
            rows = sheet.get_all_values()[1:]
            telegram_id_col_index = sheet.row_values(1).index("Telegram ID")
            for row in rows:
                if len(row) > telegram_id_col_index:
                    chat_id = row[telegram_id_col_index]
                    try:
                        await send_message_or_media(chat_id, media_type, media_file_id, msg_content)
                    except Exception as e:
                        logging.error(f"Failed to send message to {chat_id}: {e}")
            await message.answer("Message sent to all registered users.")
        else:
            # Targets is a space-separated list of Unique IDs
            unique_ids = targets.split()
            rows = sheet.get_all_values()
            headers = rows[0]
            unique_id_index = headers.index("Unique ID")
            telegram_id_index = headers.index("Telegram ID")

            for uid in unique_ids:
                for row in rows[1:]:
                    if len(row) > unique_id_index and row[unique_id_index] == uid:
                        chat_id = row[telegram_id_index]
                        try:
                            await send_message_or_media(chat_id, media_type, media_file_id, msg_content)
                            break
                        except Exception as e:
                            logging.error(f"Failed to send message to {chat_id}: {e}")
                else:
                    await message.answer(f"Unique ID {uid} not found.")

            await message.answer("Message sent to specified users.")
    except ValueError as e:
        await message.answer(str(e))
    except Exception as e:
        logging.error(f"Unexpected error: {e}")
        await message.answer("An unexpected error occurred while processing your command.")

async def send_message_or_media(chat_id, media_type, media_file_id, caption):
    try:
        if media_type == "photo":
            await bot.send_photo(chat_id, photo=media_file_id, caption=caption)
        elif media_type == "video":
            await bot.send_video(chat_id, video=media_file_id, caption=caption)
        elif media_type == "audio":
            await bot.send_audio(chat_id, audio=media_file_id, caption=caption)
        elif media_type == "document":
            await bot.send_document(chat_id, document=media_file_id, caption=caption)
        else:
            await bot.send_message(chat_id, text=caption)
    except Exception as e:
        logging.error(f"Error while sending to chat_id {chat_id}: {e}")


################################################################################
# 10) Main Entrypoint
################################################################################

async def main():
    dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("Bot is starting polling...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
