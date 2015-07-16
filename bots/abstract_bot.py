# In The Name Of God
# ========================================
# [] File Name : abstract_bot.py
#
# [] Creation Date : 27-07-2015
#
# [] Created By : Parham Alvani (parham.alvani@gmail.com)
# =======================================
__author__ = 'Parham Alvani'

import bots.base_bot
import tgl


class AbstractBot(metaclass=bots.base_bot.BaseBot):
    bot_name = 'abstract'

    def run_query(self, query: str, msg: tgl.Msg, peer: tgl.Peer):
        pass
