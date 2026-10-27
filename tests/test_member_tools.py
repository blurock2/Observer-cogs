import asyncio
import time
from datetime import datetime, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import discord
import pytest

from cogs.afk import AFK
from cogs.Bookmarks import Bookmarks
from cogs.member_tools import ConfirmClearView
from cogs.reminder import Reminder, ReminderSeenView
from cogs.todo import Todo, TodoPager, _parse_due
from database import connect_sqlite


@pytest.fixture
def features(tmp_path, monkeypatch):
    path = str(tmp_path / 'bot.db')
    for name in ('Bookmarks','todo','afk','reminder'):
        module = __import__('cogs.'+name, fromlist=['DB_PATH'])
        monkeypatch.setattr(module, 'DB_PATH', path)
    for cls in (Bookmarks, Todo, AFK, Reminder):
        monkeypatch.setitem(cls.__init__.__globals__, 'DB_PATH', path)
    bot = NS(add_view=Mock(), tree=NS(remove_command=Mock()), wait_until_ready=AsyncMock())
    return Bookmarks(bot), Todo(bot), AFK(bot), Reminder(bot)


def interaction(user_id=1):
    return NS(user=NS(id=user_id), response=NS(send_message=AsyncMock(), edit_message=AsyncMock()), followup=NS(send=AsyncMock()))


def saved_message(id=7):
    return NS(id=id,guild=NS(id=9),channel=NS(id=10),author=NS(id=11),content='A useful guide',attachments=[],jump_url=f'https://discord.com/channels/9/10/{id}',created_at=datetime.now(timezone.utc))


@pytest.mark.asyncio
async def test_bookmark_deduplication_search_tags_and_owner_isolation(features):
    bookmarks, *_ = features
    owner = interaction()
    await bookmarks.bookmark_message(owner,saved_message())
    await bookmarks.bookmark_message(owner,saved_message())
    rows = bookmarks._find(1)
    assert len(rows)==1
    await Bookmarks.bookmark_edit.callback(bookmarks,owner,rows[0]['id'],folder='Guides',tags='Python, discord, python',note='Read this later')
    assert len(bookmarks._find(1,search='later',folder='guides',tag='PYTHON'))==1
    assert not bookmarks._find(1,search='%')  # search is literal, not a wildcard
    assert not bookmarks._find(1,tag='py')  # exact tag match
    stranger = interaction(2)
    await Bookmarks.bookmark_edit.callback(bookmarks,stranger,rows[0]['id'],note='hijacked')
    await Bookmarks.bookmark_delete.callback(bookmarks,stranger,rows[0]['id'])
    assert bookmarks._get(rows[0]['id'],1)['note']=='Read this later'
    assert not bookmarks._find(2)


@pytest.mark.asyncio
async def test_bookmark_navigation_and_deletion(features):
    bookmarks, *_ = features
    owner = interaction()
    for id in range(12):
        await bookmarks.bookmark_message(owner,saved_message(id))
    await Bookmarks.bookmarks.callback(bookmarks,owner)
    view = owner.response.send_message.call_args.kwargs['view']
    assert len(view.ids)==12
    assert await view.interaction_check(interaction(2)) is False
    await view._next(owner)
    next_view = owner.response.edit_message.call_args.kwargs['view']
    assert next_view.bookmark_id != view.bookmark_id
    await next_view._delete(owner)
    assert len(bookmarks._find(1))==11
    assert owner.response.edit_message.call_args.kwargs['view'].bookmark_id != next_view.bookmark_id


@pytest.mark.asyncio
async def test_clear_confirmation_is_owner_only(features):
    bookmarks, *_ = features
    owner = interaction()
    await bookmarks.bookmark_message(owner,saved_message())
    await Bookmarks.bookmark_clear.callback(bookmarks,owner)
    view = owner.response.send_message.call_args.kwargs['view']
    assert isinstance(view,ConfirmClearView)
    assert len(bookmarks._find(1))==1
    assert not await view.interaction_check(interaction(2))
    await view.children[1].callback(owner)
    assert len(bookmarks._find(1))==1
    await Bookmarks.bookmark_clear.callback(bookmarks,owner)
    view = owner.response.send_message.call_args.kwargs['view']
    await view.children[0].callback(owner)
    assert not bookmarks._find(1)


@pytest.mark.asyncio
async def test_todo_lists_priorities_pagination_and_edit_ownership(features):
    _, todo, *_ = features
    owner = interaction()
    for id in range(8):
        await Todo.add.callback(todo,owner,f'Task {id}',list_name='School',priority=2 if id==7 else 0)
    rows = todo._list(1,'school','active')
    assert rows[0]['text']=='Task 7'
    await Todo.list.callback(todo,owner,list_name='School')
    embed = owner.response.send_message.call_args.kwargs['embed']
    assert embed.description.count('⬜')==5
    view = owner.response.send_message.call_args.kwargs['view']
    assert isinstance(view,TodoPager) and view.pages==2
    await view._next(owner)
    assert owner.response.edit_message.call_args.kwargs['embed'].description.count('⬜')==3
    await Todo.edit.callback(todo,interaction(2),rows[0]['id'],new_text='Hijacked')
    assert todo._list(1)[0]['text']=='Task 7'
    await Todo.edit.callback(todo,owner,rows[0]['id'],due='2h',priority=1)
    edited = next(r for r in todo._list(1) if r['id']==rows[0]['id'])
    assert edited['due_at']>time.time()+7100


@pytest.mark.asyncio
async def test_recurring_todo_is_atomic_and_does_not_duplicate_on_reopen(features):
    _, todo, *_ = features
    owner = interaction()
    await Todo.add.callback(todo,owner,'Weekly cleanup',due='1h',repeat='7d')
    first = todo._list(1)[0]
    assert todo._complete(2,first['id'],True) is None
    successor = todo._complete(1,first['id'],True)
    assert successor and len(todo._list(1))==2
    assert todo._complete(1,first['id'],True)==0
    todo._complete(1,first['id'],False)
    todo._complete(1,first['id'],True)
    assert len(todo._list(1))==2
    assert len(todo._list(1,status='active'))==1
    assert len(todo._list(1,status='completed'))==1
    assert todo._list(1,status='active')[0]['due_at']>time.time()


@pytest.mark.asyncio
async def test_invalid_todo_input_does_not_write(features):
    _, todo, *_ = features
    for text,due,repeat in [('  ',None,None),('x','invalid',None),('x',None,'5m'),('x'*401,None,None)]:
        await Todo.add.callback(todo,interaction(),text,due=due,repeat=repeat)
    assert not todo._list(1)
    assert _parse_due('not a date') is None
    assert _parse_due('2026-10-12T18:30+02:00')==datetime(2026,10,12,16,30,tzinfo=timezone.utc).timestamp()


def test_reminder_snooze_revision_ownership_and_delivery_race(features):
    *_, reminder = features
    id = reminder._create_reminder(1,'task',60)
    original = reminder._get_reminder(id)
    assert reminder._change_reminder(id,2,0,'seen') is None
    deadline = reminder._change_reminder(id,1,0,'snooze10')
    assert deadline>time.time()+590
    assert reminder._change_reminder(id,1,0,'seen') is None
    reminder._set_next_send(id,time.time()+99999,original['revision'])
    assert reminder._get_reminder(id)['next_send_at']==deadline
    assert reminder._change_reminder(id,1,1,'seen')==0
    assert reminder._get_reminder(id)['acknowledged']==1


def test_recurring_reminder_keeps_future_schedule_and_stop_ends_it(features):
    *_, reminder = features
    id = reminder._create_reminder(1,'daily task',60,repeat_seconds=86400)
    deadline = reminder._change_reminder(id,1,0,'seen')
    row = reminder._get_reminder(id)
    assert deadline==row['cycle_at'] and deadline>time.time()
    assert row['acknowledged']==0 and row['revision']==1
    assert reminder._change_reminder(id,1,0,'seen') is None
    assert reminder._change_reminder(id,1,1,'stop')==0
    assert reminder._get_reminder(id)['acknowledged']==1


@pytest.mark.asyncio
async def test_reminder_buttons_persist_and_stale_notifications_are_rejected(features):
    *_, reminder = features
    id = reminder._create_reminder(1,'task',60)
    view = ReminderSeenView(reminder,id)
    assert view.is_persistent() and len(view.children)==4
    assert 'reminder:seen:'+str(id) in [v.children[0].custom_id for v in (ReminderSeenView(reminder,id,legacy=True),)]
    owner = interaction()
    await view._action(owner,'snooze60')
    assert reminder._get_reminder(id)['revision']==1
    await view._action(owner,'seen')
    assert 'outdated' in owner.response.send_message.call_args.args[0]
    restored = Reminder(reminder.bot)
    new = ReminderSeenView(restored,id)
    assert new.children[0].custom_id.endswith(':1')


@pytest.mark.asyncio
async def test_reminder_reschedule_and_delete_do_not_modify_others(features):
    *_, reminder = features
    id = reminder._create_reminder(1,'task',60)
    reminder._register_view(id)
    view = reminder._views[id][0]
    await Reminder.reminder_delete.callback(reminder,interaction(2),id)
    assert not view.is_finished()
    before = reminder._get_reminder(id)['next_send_at']
    await Reminder.reminder_reschedule.callback(reminder,interaction(2),id,'1d')
    assert reminder._get_reminder(id)['next_send_at']==before
    await Reminder.reminder_reschedule.callback(reminder,interaction(1),id,'2h')
    assert reminder._get_reminder(id)['revision']==1
    assert reminder._get_reminder(id)['next_send_at']>time.time()+7100


@pytest.mark.asyncio
async def test_reminder_delivery_uses_source_link_and_private_default(features):
    *_, reminder = features
    dm = NS(send=AsyncMock())
    user = NS(create_dm=AsyncMock(return_value=dm))
    reminder.bot.get_user = Mock(return_value=user)
    id = reminder._create_reminder(1,'saved task',1)
    with connect_sqlite(reminder._database_path) as db:
        db.execute('UPDATE reminders SET source_url=? WHERE id=?', ('https://discord.com/channels/1/2/3',id))
    row = reminder._get_reminder(id)
    assert row['public_fallback']==0
    await reminder._send_reminder(row)
    embed = dm.send.call_args.kwargs['embed']
    assert embed.fields[0].value.endswith('(https://discord.com/channels/1/2/3)')
    assert reminder._get_reminder(id)['next_send_at']>time.time()+3500


@pytest.mark.asyncio
async def test_afk_mentions_are_bounded_grouped_and_permission_filtered(features):
    _,_,afk,_ = features
    target = NS(id=1,display_name='Away User')
    member = NS(id=2,bot=False)
    visible = NS(permissions_for=lambda _: NS(view_channel=True),send=AsyncMock(),id=10)
    hidden = NS(permissions_for=lambda _: NS(view_channel=False),send=AsyncMock(),id=20)
    guild = NS(id=9,get_member=lambda _:target,get_channel_or_thread=lambda id: visible if id==10 else hidden)
    row = {'user_id':1,'reason':'Studying','started_at':time.time(),'return_at':None}
    message = NS(guild=guild,author=member,channel=visible,id=100,jump_url='https://discord.com/channels/9/10/100')
    now = time.time()
    assert afk._record_mention(message,row,now)
    message.id=101
    assert not afk._record_mention(message,row,now+1)
    assert len(afk._mentions(9,1))==2
    for i in range(40):
        message.id=200+i
        afk._record_mention(message,row,now+2+i)
    assert len(afk._mentions(9,1))==25
    assert 'Jump' in afk._summary(guild,target).description
    with connect_sqlite(afk.db_path) as db:
        db.execute('UPDATE afk_mentions SET channel_id=20')
    assert 'No recent mentions' in afk._summary(guild,target).description


@pytest.mark.asyncio
async def test_afk_preserves_manual_nickname_change_and_returns_once(features):
    _,_,afk,_ = features
    member = Mock(spec=discord.Member)
    member.id=1
    member.guild=NS(id=9)
    member.nick='New nickname set by staff'
    member.edit=AsyncMock()
    with connect_sqlite(afk.db_path) as db:
        db.execute('INSERT INTO afk_users (guild_id,user_id,reason,started_at,original_nick,applied_nick) VALUES (9,1,?,?,?,?)',('AFK',time.time(),'Original','[AFK] Original'))
    assert await afk._return(member)
    member.edit.assert_not_awaited()
    assert await afk._return(member) is None


def test_all_four_migrations_preserve_legacy_data_and_can_run_twice(tmp_path,monkeypatch):
    path = str(tmp_path/'legacy.db')
    for name in ('Bookmarks','todo','afk','reminder'):
        module = __import__('cogs.'+name,fromlist=['DB_PATH'])
        monkeypatch.setattr(module,'DB_PATH',path)
    for cls in (Bookmarks, Todo, AFK, Reminder):
        monkeypatch.setitem(cls.__init__.__globals__, 'DB_PATH', path)
    with connect_sqlite(path) as db:
        db.execute('CREATE TABLE bookmarks(id INTEGER PRIMARY KEY,user_id INTEGER,guild_id INTEGER,channel_id INTEGER,message_id INTEGER,author_id INTEGER,content TEXT,attachment_urls TEXT,message_url TEXT,bookmarked_at REAL,original_timestamp REAL)')
        db.execute("INSERT INTO bookmarks VALUES(1,1,9,10,7,11,'legacy bookmark','','https://discord.com',1,1)")
        db.execute('CREATE TABLE todos(id INTEGER PRIMARY KEY,user_id INTEGER,text TEXT,created_at REAL,due_at REAL,completed INTEGER,completed_at REAL)')
        db.execute("INSERT INTO todos VALUES(1,1,'legacy task',1,NULL,0,NULL)")
        db.execute('CREATE TABLE afk_users(guild_id INTEGER,user_id INTEGER,reason TEXT,started_at REAL,original_nick TEXT,PRIMARY KEY(guild_id,user_id))')
        db.execute("INSERT INTO afk_users VALUES(9,1,'legacy AFK',1,NULL)")
        db.execute('CREATE TABLE reminders(id INTEGER PRIMARY KEY,user_id INTEGER,content TEXT,next_send_at REAL,acknowledged INTEGER,created_at REAL)')
        db.execute("INSERT INTO reminders VALUES(1,1,'legacy reminder',100,0,1)")
    bot=NS()
    for _ in range(2):
        bookmarks,todo,_afk,reminder=Bookmarks(bot),Todo(bot),AFK(bot),Reminder(bot)
        assert bookmarks._get(1,1)['folder']=='Inbox'
        assert todo._list(1)[0]['list_name']=='Personal'
        row=reminder._get_reminder(1)
        assert row['content']=='legacy reminder' and row['revision']==0 and row['cycle_at']==100
        assert row['public_fallback']==1
        with connect_sqlite(path) as db:
            assert db.execute('SELECT reason FROM afk_users').fetchone()[0]=='legacy AFK'


@pytest.mark.asyncio
async def test_extensions_load_reload_and_standalone_bookmark_binding(features):
    from discord.ext import commands
    async with commands.Bot(command_prefix='!',intents=discord.Intents.none()) as bot:
        for name in ('cogs.Bookmarks','cogs.reminder','cogs.todo','cogs.afk'):
            await bot.load_extension(name)
            await bot.reload_extension(name)
        assert bot.tree.get_command('bookmark').get_command('delete').binding is bot.get_cog('Bookmarks')
        assert bot.tree.get_command('bookmark').get_command('clear').binding is bot.get_cog('Bookmarks')
        assert bot.tree.get_command('todo').get_command('edit')
        assert bot.tree.get_command('afk-back')
        assert len(bot.tree.get_commands(type=discord.AppCommandType.message))==2
        await asyncio.sleep(0)
