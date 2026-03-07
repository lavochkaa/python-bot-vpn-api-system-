from aiogram.fsm.state import State, StatesGroup


class AdminTicketStates(StatesGroup):
    waiting_reply_text = State()
    waiting_ticket_search = State()
    waiting_user_id = State()
    waiting_balance_amount = State()
    waiting_days_amount = State()
    waiting_promo_code = State()
    waiting_promo_target = State()
    waiting_promo_value = State()
    waiting_promo_expires = State()
    waiting_promo_limit = State()
    waiting_promo_edit_value = State()
    waiting_promo_edit_expires = State()
    waiting_promo_edit_limit = State()
