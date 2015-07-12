# In The Name Of God
# ========================================
# [] File Name : bot.py
#
# [] Creation Date : 26-07-2015
#
# [] Created By : Parham Alvani (parham.alvani@gmail.com)
# =======================================
__author__ = 'Parham Alvani'
import tgl


class MainBot:
    our_id = 0
    modules = []

    @staticmethod
    def on_our_id(user_id):
        """
        Informs about id of currently logged in user.|
        :param user_id: int
        :return:
        """
        MainBot.our_id = user_id
        return "Set ID: " + str(MainBot.our_id)

    @staticmethod
    def on_msg_receive(message):
        """
        This is called when we receive new `tgl.Msg` object
        (*may be called before on_binlog_replay_end, than it is old msg*).
        :param message: tgl.Msg
        :return:
        """

        if message.dest.id == MainBot.our_id:  # Direct message
            peer = message.src
        else:  # Group message
            peer = message.dest
        for module in MainBot.modules:
            if message.text is not None and message.text.startswith("!" + module.bot_name):
                query = message.text[1 + len(module.bot_name):]
                query = query.lstrip()
                query = query.rstrip()
                module().run_query(query, message, peer)

    @staticmethod
    def on_user_update(peer, what_changed):
        """
        updated info about user. peer is a object representing the user.
        :param peer: tgl.Peer
        :param what_changed: array of strings
        :return:
        """
        if peer.username == 'mrma95':
            if peer.user_status['online']:
                peer.send_msg("Salute !, You went online.... at {}".format(peer.user_status['when']))


import bots

# Set callbacks
tgl.set_on_msg_receive(MainBot.on_msg_receive)
tgl.set_on_our_id(MainBot.on_our_id)
tgl.set_on_user_update(MainBot.on_user_update)
