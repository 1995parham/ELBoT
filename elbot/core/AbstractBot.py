# In The Name Of God
# ========================================
# [] File Name : AbstractBot.py
#
# [] Creation Date : 09-09-2015
#
# [] Created By : Parham Alvani (parham.alvani@gmail.com)
# =======================================
__author__ = 'Parham Alvani'

from .Message import Message
from .BotFather import BotFather


class AbstractBot:
    bot_name = 'abstract'

    def run_query(self, message: Message, father: BotFather):
        raise NotImplementedError
