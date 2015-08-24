# In The Name Of God
# ========================================
# [] File Name : file.py
#
# [] Creation Date : 27-07-2015
#
# [] Created By : Parham Alvani (parham.alvani@gmail.com)
# =======================================
__author__ = 'Parham Alvani'

import os
import bots.abstract_bot
import tgl


class FTPBot(bots.abstract_bot.AbstractBot):
    bot_name = 'ftp'
    authorize_users = ['parham_alvani']

    def __init__(self):
        self.msg = None
        self.peer = None

    def run_query(self, query: str, msg: tgl.Msg, peer: tgl.Peer):
        self.msg = msg
        self.peer = peer
        if self.msg.src.username in FTPBot.authorize_users:
            command = query.split(' ')[0]
            try:
                method = getattr(self, 'do_' + command)
            except AttributeError:
                method = None
            if not method:
                self.peer.send_msg("Error: Command not found [{}]".format(command), reply=self.msg.id)
                return
            method(query[len(command):].strip())
        else:
            self.peer.send_msg("You don not have permission on FTP server", reply=self.msg.id)

    def do_ls(self, query: str):
        path = query.lstrip()
        if not os.path.exists(path):
            self.peer.send_msg("Error: Path not found [{}]".format(path), reply=self.msg.id)
            return
        reply = ""
        for file in os.listdir(path):
            if not os.path.isdir(os.path.join(path, file)):
                reply += "{1}[{0} B]\n".format(os.lstat(os.path.join(path, file)).st_size, file)
            else:
                reply += "{0}[-]\n".format(file)
        self.peer.send_msg(reply, reply=self.msg.id)

    def do_get(self, query: str):
        path = query.lstrip()
        if not os.path.exists(path):
            self.peer.send_msg("Error: Path not found [{}]".format(path), reply=self.msg.id)
            return
        if os.path.isdir(path):
            self.peer.send_msg("Error: Cannot send directory [{}]".format(path), reply=self.msg.id)
            return
        self.peer.send_document(path)
