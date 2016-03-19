# In The Name Of God
# ========================================
# [] File Name : AbstractBot.py
#
# [] Creation Date : 09-09-2015
#
# [] Created By : Parham Alvani (parham.alvani@gmail.com)
# =======================================

from elbot.core.Message import Message
from elbot.core.BotFather import BotFather


class AbstractBot:
    bot_name = 'abstract'

    def run_query(self, message: Message, father: BotFather):
        raise NotImplementedError
