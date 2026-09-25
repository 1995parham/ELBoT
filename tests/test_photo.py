"""Which RPC a chat photo change goes through.

A basic group and a supergroup are different Telegram types taking different
requests with different parameter names, and sending the wrong one fails as a
type error rather than a permission error -- which reads as a broken tool
rather than a wrong call. The choice is a pure function precisely so it can be
checked without a network.
"""

import pytest
from telethon.tl.functions.channels import EditPhotoRequest
from telethon.tl.functions.messages import EditChatPhotoRequest
from telethon.tl.types import Channel, Chat, ChatPhotoEmpty, InputChatPhotoEmpty, User

import topoli_user as t


def a_chat() -> Chat:
    return Chat(
        id=5454320239,
        title="مهاجرت همروش - فنی",
        photo=ChatPhotoEmpty(),
        participants_count=3,
        date=None,
        version=1,
    )


def a_channel() -> Channel:
    return Channel(
        id=3953756379,
        title="ParadiseHub <> Raha Cloud <> Hamravesh",
        photo=ChatPhotoEmpty(),
        date=None,
        megagroup=True,
    )


def test_a_basic_group_goes_through_messages_edit_chat_photo():
    req = t.photo_request(a_chat(), InputChatPhotoEmpty())
    assert isinstance(req, EditChatPhotoRequest)
    assert req.chat_id == 5454320239


def test_a_supergroup_goes_through_channels_edit_photo():
    req = t.photo_request(a_channel(), InputChatPhotoEmpty())
    assert isinstance(req, EditPhotoRequest)


def test_a_private_chat_is_refused_rather_than_silently_changing_your_avatar():
    """photos.UploadProfilePhoto is a different call with a wider blast radius."""
    user = User(id=1, first_name="x")
    with pytest.raises(SystemExit):
        t.photo_request(user, InputChatPhotoEmpty())
