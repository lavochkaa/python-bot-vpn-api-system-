from aiogram.fsm.state import State, StatesGroup


class ReviewStates(StatesGroup):
    waiting_rating = State()
    waiting_text = State()
