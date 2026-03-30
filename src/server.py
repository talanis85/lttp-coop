import aioconsole
import argparse
import asyncio
import functools
import json
import logging
import re
import shlex
import ssl
import urllib.request
import websockets
import zlib
import base64

from games.alttp import Alttp

class Client:
    def __init__(self, socket):
        self.socket = socket
        self.auth = False
        self.name = None
        self.team = None
        self.slot = 0

class Context:
    def __init__(self, host, port, password):
        self.player_names = {}
        self.rom_names = {}
        self.host = host
        self.port = port
        self.password = password
        self.server = None
        self.countdown_timer = 0
        self.clients = []

        self.game = Alttp()

async def send_msgs(websocket, msgs):
    if not websocket or not websocket.open or websocket.closed:
        return
    try:
        await websocket.send(json.dumps(msgs))
    except websockets.ConnectionClosed:
        pass

def broadcast_all(ctx : Context, msgs):
    for client in ctx.clients:
        if client.auth:
            asyncio.create_task(send_msgs(client.socket, msgs))

def broadcast_others(ctx : Context, ex, msgs):
    for client in ctx.clients:
        if client == ex: continue

        if client.auth:
            asyncio.create_task(send_msgs(client.socket, msgs))

def broadcast_team(ctx : Context, team, msgs):
    for client in ctx.clients:
        if client.auth and client.team == team:
            asyncio.create_task(send_msgs(client.socket, msgs))

def notify_all(ctx : Context, text):
    logging.info("Notice (all): %s" % text)
    broadcast_all(ctx, [['Print', text]])

def notify_team(ctx : Context, team : int, text : str):
    logging.info("Notice (Team #%d): %s" % (team+1, text))
    broadcast_team(ctx, team, [['Print', text]])

def notify_client(client : Client, text : str):
    if not client.auth:
        return
    logging.info("Notice (Player %s in team %d): %s" % (client.name, client.team+1, text))
    asyncio.create_task(send_msgs(client.socket,  [['Print', text]]))

async def server(websocket, path, ctx : Context):
    client = Client(websocket)
    ctx.clients.append(client)

    try:
        await on_client_connected(ctx, client)
        async for data in websocket:
            for msg in json.loads(data):
                if len(msg) == 1:
                    cmd = msg[0]
                    args = None
                else:
                    cmd = msg[0]
                    args = msg[1]
                await process_client_cmd(ctx, client, cmd, args)
    except Exception as e:
        if not isinstance(e, websockets.WebSocketException):
            logging.exception(e)
    finally:
        await on_client_disconnected(ctx, client)
        ctx.clients.remove(client)

async def on_client_connected(ctx : Context, client : Client):
    logging.info("Sending RoomInfo")
    await send_msgs(client.socket, [['RoomInfo', {
        'password': ctx.password is not None,
        'players': [(client.team, client.slot, client.name) for client in ctx.clients if client.auth]
    }]])

async def on_client_disconnected(ctx : Context, client : Client):
    if client.auth:
        await on_client_left(ctx, client)

async def on_client_joined(ctx : Context, client : Client):
    notify_all(ctx, "%s (Team #%d) has joined the game" % (client.name, client.team + 1))

async def on_client_left(ctx : Context, client : Client):
    notify_all(ctx, "%s (Team #%d) has left the game" % (client.name, client.team + 1))

async def countdown(ctx : Context, timer):
    notify_all(ctx, f'[Server]: Starting countdown of {timer}s')
    if ctx.countdown_timer:
        ctx.countdown_timer = timer
        return

    ctx.countdown_timer = timer
    while ctx.countdown_timer > 0:
        notify_all(ctx, f'[Server]: {ctx.countdown_timer}')
        ctx.countdown_timer -= 1
        await asyncio.sleep(1)
    notify_all(ctx, f'[Server]: GO')

def get_connected_players_string(ctx : Context):
    auth_clients = [c for c in ctx.clients if c.auth]
    if not auth_clients:
        return 'No player connected'

    auth_clients.sort(key=lambda c: (c.team, c.slot))
    current_team = 0
    text = 'Team #1: '
    for c in auth_clients:
        if c.team != current_team:
            text += f':: Team #{c.team + 1}: '
            current_team = c.team
        text += f'{c.name} '
    return 'Connected players: ' + text[:-1]

async def process_client_cmd(ctx : Context, client : Client, cmd, args):
    if type(cmd) is not str:
        logging.info(str(cmd))
        await send_msgs(client.socket, [['InvalidCmd']])
        return

    if cmd == 'Connect':
        logging.info("Connection attempt")
        if not args or type(args) is not dict or \
                'password' not in args or type(args['password']) not in [str, type(None)] or \
                'name' not in args or type(args['name']) is not str:
            await send_msgs(client.socket, [['InvalidArguments', 'Connect']])
            return

        errors = set()
        if ctx.password is not None and args['password'] != ctx.password:
            errors.add('InvalidPassword')

        client.team = 0
        client.name = args['name']

        if errors:
            await send_msgs(client.socket, [['ConnectionRefused', list(errors)]])
        else:
            client.auth = True
            reply = [['Connected', [client.team, [c.name for c in ctx.clients]]]]
            await send_msgs(client.socket, reply)
            await on_client_joined(ctx, client)

    if not client.auth:
        return

    if cmd == 'Sync':
        items = []
        for ram in ctx.game.locations:
            if ram.initialized:
                items.append([ram.address, base64.b64encode(ram.value).decode('ascii')])
        logging.info("Syncing: " + str(items))
        await send_msgs(client.socket, [['Sync', [items]]])

    if cmd == 'Update':
        address = args[0]
        diff = args[1]
        ram = ctx.game.lookupRAM(address)
        if ram is None:
            logging.error("Received update for unknown RAM location: %s" % hex(address))
        else:
            logging.info("Update from %s: %x -> %s" % (client.name, address, ram.format_diff(diff)))
            ram.apply_diff(diff)
            ram.initialized = True
            broadcast_others(ctx, client, [['Update', [address, diff]]])

    if cmd == 'Say':
        if type(args) is not str or not args.isprintable():
            await send_msgs(client.socket, [['InvalidArguments', 'Say']])
            return

        notify_all(ctx, client.name + ': ' + args)

        if args.startswith('!players'):
            notify_all(ctx, get_connected_players_string(ctx))
        if args.startswith('!forfeit'):
            if ctx.disable_client_forfeit:
                notify_client(client, 'Client-initiated forfeits are disabled.  Please ask the host of this game to forfeit on your behalf.')
            else:
                forfeit_player(ctx, client.team, client.slot)
        if args.startswith('!countdown'):
            try:
                timer = int(args.split()[1])
            except (IndexError, ValueError):
                timer = 10
            asyncio.create_task(countdown(ctx, timer))

def set_password(ctx : Context, password):
    ctx.password = password
    logging.warning('Password set to ' + password if password is not None else 'Password disabled')

async def console(ctx : Context):
    while True:
        input = await aioconsole.ainput()

        command = shlex.split(input)
        if not command:
            continue

        if command[0] == '/exit':
            ctx.server.ws_server.close()
            break

        if command[0] == '/players':
            logging.info(get_connected_players_string(ctx))
        if command[0] == '/password':
            set_password(ctx, command[1] if len(command) > 1 else None)
        if command[0] == '/kick' and len(command) > 1:
            team = int(command[2]) - 1 if len(command) > 2 and command[2].isdigit() else None
            for client in ctx.clients:
                if client.auth and client.name.lower() == command[1].lower() and (team is None or team == client.team):
                    if client.socket and not client.socket.closed:
                        await client.socket.close()

        if command[0][0] != '/':
            notify_all(ctx, '[Server]: ' + input)

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default=None)
    parser.add_argument('--port', default=38281, type=int)
    parser.add_argument('--password', default=None)
    parser.add_argument('--loglevel', default='info', choices=['debug', 'info', 'warning', 'error', 'critical'])
    args = parser.parse_args()

    logging.basicConfig(format='[%(asctime)s] %(message)s', level=getattr(logging, args.loglevel.upper(), logging.INFO))

    ctx = Context(args.host, args.port, args.password)

    ip = urllib.request.urlopen('https://v4.ident.me', context=ssl._create_unverified_context()).read().decode('utf8') if not ctx.host else ctx.host
    logging.info('Hosting game at %s:%d (%s)' % (ip, ctx.port, 'No password' if not ctx.password else 'Password: %s' % ctx.password))

    ctx.server = websockets.serve(functools.partial(server,ctx=ctx), ctx.host, ctx.port, ping_timeout=None, ping_interval=None)
    await ctx.server
    await console(ctx)

if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
    loop.run_until_complete(asyncio.gather(*asyncio.Task.all_tasks()))
    loop.close()
