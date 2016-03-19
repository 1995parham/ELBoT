# In The Name Of God
# ========================================
# [] File Name : AbstractBot.py
#
# [] Creation Date : 09-09-2015
#
# [] Created By : Parham Alvani (parham.alvani@gmail.com)
# =======================================

from elbot.core.BotFather import BotFather
from elbot.domain.Message import Message


class AbstractBot:
    bot_name = 'abstract'

    def run_query(self, message: Message, father: BotFather):
        raise NotImplementedError
