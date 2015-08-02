# In The Name Of God
# ========================================
# [] File Name : history_bot.py
#
# [] Creation Date : 27-07-2015
#
# [] Created By : Parham Alvani (parham.alvani@gmail.com)
# =======================================
__author__ = 'Parham Alvani'

import tgl
import bots.abstract_bot
import functools
import time


class HistoryBot(bots.abstract_bot.AbstractBot):
    bot_name = 'history'

    def __init__(self):
        self.peer = None
        self.history = []

    def run_query(self, query: str, msg: tgl.Msg, peer: tgl.Peer):
        self.peer = peer

        # Get all the history, 100 msgs at a time
        self.peer.history(0, 100, functools.partial(self.history_callback, 100))

    def history_callback(self, msg_count, success, msgs: tgl.Msg):
        self.history.extend(msgs)
        if len(msgs) == msg_count:
            self.peer.history(len(self.history), msg_count,
                              functools.partial(self.history_callback, msg_count))
        else:
            output = open("history_" + self.peer.name + ".txt", "w")
            self.history.reverse()
            for msg in self.history:
                line = "[{0}] |{1}| {2} --> {3} >> {4}\n".format(msg.id, time.strftime("%c", msg.date.timetuple()),
                                                                 msg.src.name.replace("_", " "),
                                                                 msg.dest.name.replace("_", " "), msg.text)
                output.write(line)
