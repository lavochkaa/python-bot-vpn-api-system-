from aiogram.fsm.state import State, StatesGroup


class AdminTicketStates(StatesGroup):
    waiting_reply_text = State()
    waiting_user_id = State()
    waiting_balance_amount = State()
    waiting_days_amount = State()
