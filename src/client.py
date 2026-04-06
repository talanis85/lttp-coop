import aioconsole
import argparse
import asyncio
import colorama
import json
import logging
import shlex
import urllib.parse
import websockets
import base64

from games.alttp import Alttp

class ReceivedItem:
    def __init__(self, item, location, player):
        self.item = item
        self.location = location
        self.player = player

class Context:
    def __init__(self, snes_address, server_address, password):
        self.snes_address = snes_address
        self.server_address = server_address

        self.exit_event = asyncio.Event()
        self.watcher_event = asyncio.Event()

        self.input_queue = asyncio.Queue()
        self.input_requests = 0

        self.snes_socket = None
        self.snes_state = SNES_DISCONNECTED
        self.snes_attached_device = None
        self.snes_reconnect_address = None
        self.snes_recv_queue = asyncio.Queue()
        self.snes_request_lock = asyncio.Lock()
        self.is_sd2snes = False
        self.snes_write_buffer = []

        self.server_task = None
        self.socket = None
        self.password = password

        self.team = None
        self.slot = None
        self.player_names = {}
        self.locations_checked = set()
        self.locations_scouted = set()
        self.items_received = []
        self.locations_info = {}
        self.awaiting_rom = False
        self.rom = None
        self.auth = None
        self.total_locations = None
        self.mode_flags = None
        self.key_drop_mode = False
        self.shop_mode = False
        self.retro_mode = False
        self.pottery_mode = False
        self.mystery_mode = False
        self.ignore_count = 0

        # self.state = 0
        # self.ram = bytearray([0 for x in range(0x1000)])
        self.game = Alttp()
        self.game_lock = asyncio.Lock()
        self.name = "dummy"
        self.synced = asyncio.Event()

        self.lookup_name_to_id = {}
        self.lookup_id_to_name = {}

def color_code(*args):
    codes = {'reset': 0, 'bold': 1, 'underline': 4, 'black': 30, 'red': 31, 'green': 32, 'yellow': 33, 'blue': 34,
             'magenta': 35, 'cyan': 36, 'white': 37 , 'black_bg': 40, 'red_bg': 41, 'green_bg': 42, 'yellow_bg': 43,
             'blue_bg': 44, 'purple_bg': 45, 'cyan_bg': 46, 'white_bg': 47}
    return '\033[' + ';'.join([str(codes[arg]) for arg in args]) + 'm'

def color(text, *args):
    return color_code(*args) + text + color_code('reset')

RECONNECT_DELAY = 30

ROM_START = 0x000000
WRAM_START = 0xF50000
WRAM_SIZE = 0x20000
SRAM_START = 0xE00000

ROMNAME_START = SRAM_START + 0x2000
ROMNAME_SIZE = 0x15

INGAME_MODES = {0x07, 0x09, 0x0b}

SAVEDATA_START = WRAM_START + 0xF000
SAVEDATA_SIZE = 0x500

SNES_DISCONNECTED = 0
SNES_CONNECTING = 1
SNES_CONNECTED = 2
SNES_ATTACHED = 3

async def snes_connect(ctx : Context, address):
    if ctx.snes_socket is not None:
        logging.error('Already connected to snes')
        return

    ctx.snes_state = SNES_CONNECTING
    recv_task = None

    address = f"ws://{address}" if "://" not in address else address

    logging.info("Connecting to QUsb2snes at %s ..." % address)

    try:
        ctx.snes_socket = await websockets.connect(address, ping_timeout=None, ping_interval=None)
        ctx.snes_state = SNES_CONNECTED

        DeviceList_Request = {
            "Opcode" : "DeviceList",
            "Space" : "SNES"
        }
        await ctx.snes_socket.send(json.dumps(DeviceList_Request))

        reply = json.loads(await ctx.snes_socket.recv())
        devices = reply['Results'] if 'Results' in reply and len(reply['Results']) > 0 else None

        if not devices:
            raise Exception('No device found')

        logging.info("Available devices:")
        for id, device in enumerate(devices):
            logging.info("[%d] %s" % (id + 1, device))

        device = None
        if len(devices) == 1:
            device = devices[0]
        elif ctx.snes_reconnect_address:
            if ctx.snes_attached_device[1] in devices:
                device = ctx.snes_attached_device[1]
            else:
                device = devices[ctx.snes_attached_device[0]]
        else:
            while True:
                logging.info("Select a device:")
                choice = await console_input(ctx)
                if choice is None:
                    raise Exception('Abort input')
                if not choice.isdigit() or int(choice) < 1 or int(choice) > len(devices):
                    logging.warning("Invalid choice (%s)" % choice)
                    continue

                device = devices[int(choice) - 1]
                break

        logging.info("Attaching to " + device)

        Attach_Request = {
            "Opcode" : "Attach",
            "Space" : "SNES",
            "Operands" : [device]
        }
        await ctx.snes_socket.send(json.dumps(Attach_Request))
        ctx.snes_state = SNES_ATTACHED
        ctx.snes_attached_device = (devices.index(device), device)

        if 'SD2SNES'.lower() in device.lower() or (len(device) == 4 and device[:3] == 'COM'):
            logging.info("SD2SNES Detected")
            ctx.is_sd2snes = True
            await ctx.snes_socket.send(json.dumps({"Opcode" : "Info", "Space" : "SNES"}))
            reply = json.loads(await ctx.snes_socket.recv())
            if reply and 'Results' in reply:
                logging.info(reply['Results'])
        else:
            ctx.is_sd2snes = False

        ctx.snes_reconnect_address = address
        recv_task = asyncio.create_task(snes_recv_loop(ctx))

    except Exception as e:
        if recv_task is not None:
            if not ctx.snes_socket.closed:
                await ctx.snes_socket.close()
        else:
            if ctx.snes_socket is not None:
                if not ctx.snes_socket.closed:
                    await ctx.snes_socket.close()
                ctx.snes_socket = None
            ctx.snes_state = SNES_DISCONNECTED
        if not ctx.snes_reconnect_address:
            logging.error("Error connecting to snes (%s)" % e)
        else:
            logging.error(f"Error connecting to snes, attempt again in {RECONNECT_DELAY}s")
            asyncio.create_task(snes_autoreconnect(ctx))

async def snes_autoreconnect(ctx: Context):
    await asyncio.sleep(RECONNECT_DELAY)
    if ctx.snes_reconnect_address and ctx.snes_socket is None:
        await snes_connect(ctx, ctx.snes_reconnect_address)

async def snes_recv_loop(ctx : Context):
    try:
        async for msg in ctx.snes_socket:
            ctx.snes_recv_queue.put_nowait(msg)
        logging.warning("Snes disconnected")
    except Exception as e:
        if not isinstance(e, websockets.WebSocketException):
            logging.exception(e)
        logging.error("Lost connection to the snes, type /snes to reconnect")
    finally:
        socket, ctx.snes_socket = ctx.snes_socket, None
        if socket is not None and not socket.closed:
            await socket.close()

        ctx.snes_state = SNES_DISCONNECTED
        ctx.snes_recv_queue = asyncio.Queue()
        ctx.hud_message_queue = []

        if ctx.snes_reconnect_address:
            logging.info(f"...reconnecting in {RECONNECT_DELAY}s")
            asyncio.create_task(snes_autoreconnect(ctx))

async def snes_read(ctx : Context, address, size):
    try:
        await ctx.snes_request_lock.acquire()

        if ctx.snes_state != SNES_ATTACHED or ctx.snes_socket is None or not ctx.snes_socket.open or ctx.snes_socket.closed:
            return None

        GetAddress_Request = {
            "Opcode" : "GetAddress",
            "Space" : "SNES",
            "Operands" : [hex(address)[2:], hex(size)[2:]]
        }
        try:
            await ctx.snes_socket.send(json.dumps(GetAddress_Request))
        except websockets.ConnectionClosed:
            return None

        data = bytes()
        while len(data) < size:
            try:
                data += await asyncio.wait_for(ctx.snes_recv_queue.get(), 5)
            except asyncio.TimeoutError:
                break

        if len(data) != size:
            logging.error('Error reading %s, requested %d bytes, received %d' % (hex(address), size, len(data)))
            if len(data):
                logging.error(str(data))
            if ctx.snes_socket is not None and not ctx.snes_socket.closed:
                await ctx.snes_socket.close()
            return None

        return data
    finally:
        ctx.snes_request_lock.release()

async def snes_write(ctx : Context, write_list):
    try:
        await ctx.snes_request_lock.acquire()

        if ctx.snes_state != SNES_ATTACHED or ctx.snes_socket is None or not ctx.snes_socket.open or ctx.snes_socket.closed:
            return False

        PutAddress_Request = {
            "Opcode" : "PutAddress",
            "Operands" : []
        }

        if ctx.is_sd2snes:
            cmd = b'\x00\xE2\x20\x48\xEB\x48'

            for address, data in write_list:
                if (address < WRAM_START) or ((address + len(data)) > (WRAM_START + WRAM_SIZE)):
                    logging.error("SD2SNES: Write out of range %s (%d)" % (hex(address), len(data)))
                    return False
                for ptr, byte in enumerate(data, address + 0x7E0000 - WRAM_START):
                    cmd += b'\xA9' # LDA
                    cmd += bytes([byte])
                    cmd += b'\x8F' # STA.l
                    cmd += bytes([ptr & 0xFF, (ptr >> 8) & 0xFF, (ptr >> 16) & 0xFF])

            cmd += b'\xA9\x00\x8F\x00\x2C\x00\x68\xEB\x68\x28\x6C\xEA\xFF\x08'

            PutAddress_Request['Space'] = 'CMD'
            PutAddress_Request['Operands'] = ["2C00", hex(len(cmd)-1)[2:], "2C00", "1"]
            try:
                if ctx.snes_socket is not None:
                    await ctx.snes_socket.send(json.dumps(PutAddress_Request))
                if ctx.snes_socket is not None:
                    await ctx.snes_socket.send(cmd)
            except websockets.ConnectionClosed:
                return False
        else:
            PutAddress_Request['Space'] = 'SNES'
            try:
                #will pack those requests as soon as qusb2snes actually supports that for real
                for address, data in write_list:
                    PutAddress_Request['Operands'] = [hex(address)[2:], hex(len(data))[2:]]
                    if ctx.snes_socket is not None:
                        await ctx.snes_socket.send(json.dumps(PutAddress_Request))
                    if ctx.snes_socket is not None:
                        await ctx.snes_socket.send(data)
            except websockets.ConnectionClosed:
                return False

        return True
    finally:
        ctx.snes_request_lock.release()

def snes_buffered_write(ctx : Context, address, data):
    if len(ctx.snes_write_buffer) > 0 and (ctx.snes_write_buffer[-1][0] + len(ctx.snes_write_buffer[-1][1])) == address:
        ctx.snes_write_buffer[-1] = (ctx.snes_write_buffer[-1][0], ctx.snes_write_buffer[-1][1] + data)
    else:
        ctx.snes_write_buffer.append((address, data))

async def snes_flush_writes(ctx : Context):
    if not ctx.snes_write_buffer:
        return

    await snes_write(ctx, ctx.snes_write_buffer)
    ctx.snes_write_buffer = []

async def send_msgs(websocket, msgs):
    if not websocket or not websocket.open or websocket.closed:
        return
    try:
        await websocket.send(json.dumps(msgs))
    except websockets.ConnectionClosed:
        pass

async def server_loop(ctx : Context, address = None):
    if ctx.socket is not None:
        logging.error('Already connected')
        return

    if address is None:
        address = ctx.server_address

    while not address:
        logging.info('Enter multiworld server address')
        address = await console_input(ctx)

    address = f"ws://{address}" if "://" not in address else address
    port = urllib.parse.urlparse(address).port or 38281

    logging.info('Connecting to multiworld server at %s' % address)
    try:
        ctx.socket = await websockets.connect(address, port=port, ping_timeout=None, ping_interval=None)
        logging.info('Connected')
        ctx.server_address = address

        async for data in ctx.socket:
            for msg in json.loads(data):
                cmd, args = (msg[0], msg[1]) if len(msg) > 1 else (msg[0], None)
                await process_server_cmd(ctx, cmd, args)
        logging.warning('Disconnected from multiworld server, type /connect to reconnect')
    except ConnectionRefusedError:
        logging.error('Connection refused by the multiworld server')
    except (OSError, websockets.InvalidURI):
        logging.error('Failed to connect to the multiworld server')
    except Exception as e
        logging.error('Lost connection to the multiworld server, type /connect to reconnect')
        ctx.synced.clear()
        if not isinstance(e, websockets.WebSocketException):
            logging.exception(e)
    finally:
        ctx.awaiting_rom = False
        ctx.auth = None
        ctx.items_received = []
        ctx.locations_info = {}
        socket, ctx.socket = ctx.socket, None
        if socket is not None and not socket.closed:
            await socket.close()
        ctx.server_task = None
        if ctx.server_address:
            logging.info(f"... reconnecting in {RECONNECT_DELAY}s")
            asyncio.create_task(server_autoreconnect(ctx))

async def server_autoreconnect(ctx: Context):
    await asyncio.sleep(RECONNECT_DELAY)
    if ctx.server_address and ctx.server_task is None:
        ctx.server_task = asyncio.create_task(server_loop(ctx))

async def process_server_cmd(ctx : Context, cmd, args):
    if cmd == 'RoomInfo':
        logging.info('--------------------------------')
        logging.info('Room Information:')
        logging.info('--------------------------------')
        if args['password']:
            logging.info('Password required')
        if len(args['players']) < 1:
            logging.info('No player connected')
        else:
            args['players'].sort()
            current_team = 0
            logging.info('Connected players:')
            logging.info('  Team #1')
            for team, slot, name in args['players']:
                if team != current_team:
                    logging.info(f'  Team #{team + 1}')
                    current_team = team
                logging.info('    %s (Player %d)' % (name, slot))
        await server_auth(ctx, args['password'])

    if cmd == 'ConnectionRefused':
        if 'InvalidPassword' in args:
            logging.error('Invalid password')
            ctx.password = None
            await server_auth(ctx, True)
        if 'InvalidRom' in args:
            raise Exception('Invalid ROM detected, please verify that you have loaded the correct rom and reconnect your snes')
        if 'SlotAlreadyTaken' in args:
            raise Exception('Player slot already in use for that team')
        raise Exception('Connection refused by the multiworld host')

    if cmd == 'Connected':
        ctx.team = args[0]
        ctx.player_names = args[1]
        msgs = []
        if msgs:
            await send_msgs(ctx.socket, msgs)

    if cmd == 'Update':
        address = args[0]
        diff = args[1]
        async with ctx.game_lock:
            rambuffer = await snes_read(ctx, WRAM_START + ctx.game.minram, (ctx.game.maxram - ctx.game.minram))

            def readRAM(address, size):
              return rambuffer[(address - ctx.game.minram):(address - ctx.game.minram + size)]

            def writeRAM(address, value):
              snes_buffered_write(ctx, WRAM_START + address, value)

            ram = ctx.game.lookupRAM(address)
            if ram == None:
                raise Exception('Received Update for unknown RAM location: ' + hex(address))
            else:
                old = ram.value
                ram.apply_diff(diff)
                ram.initialized = True
                logging.info("Update: %s (%x) -> %s" % (ram.name, address, ram.format_diff(diff)))
                if ram.onreceive is not None:
                    ram.value = ram.to_bytes(ram.onreceive(readRAM, writeRAM, ram.from_bytes(ram.value), ram.from_bytes(old)))
                writeRAM(address, ram.value)
                await snes_flush_writes(ctx)

    if cmd == 'Sync':
        items = args[0]
        async with ctx.game_lock:
            rambuffer = await snes_read(ctx, WRAM_START + ctx.game.minram, (ctx.game.maxram - ctx.game.minram))

            def readRAM(address, size):
              return rambuffer[(address - ctx.game.minram):(address - ctx.game.minram + size)]

            def writeRAM(address, value):
              snes_buffered_write(ctx, WRAM_START + address, value)

            for i in items:
                address = i[0]
                value = base64.b64decode(i[1])
                ram = ctx.game.lookupRAM(address)
                if ram == None:
                    raise Exception('Received Sync for unknown RAM location: ' + hex(address))
                else:
                    old = ram.value
                    ram.value = value
                    ram.initialized = True
                    logging.info("Sync: %s (%x) -> %s" % (ram.name, address, ram.format(value)))
                    if ram.onreceive is not None:
                        ram.value = ram.to_bytes(ram.onreceive(readRAM, writeRAM, ram.from_bytes(ram.value), ram.from_bytes(old)))
                    writeRAM(address, ram.value)
            await snes_flush_writes(ctx)
            ctx.synced.set()

    if cmd == 'Print':
        logging.info(args)

async def server_auth(ctx : Context, password_requested):
    if password_requested and not ctx.password:
        logging.info('Enter the password required to join this game:')
        ctx.password = await console_input(ctx)
    await send_msgs(ctx.socket, [['Connect', {'password': ctx.password, 'name': ctx.name}]])

async def console_input(ctx : Context):
    ctx.input_requests += 1
    return await ctx.input_queue.get()

async def disconnect(ctx: Context):
    if ctx.socket is not None and not ctx.socket.closed:
        await ctx.socket.close()
    ctx.synced.clear()
    if ctx.server_task is not None:
        await ctx.server_task

async def connect(ctx: Context, address=None):
    await disconnect(ctx)
    ctx.server_task = asyncio.create_task(server_loop(ctx, address))

async def console_loop(ctx : Context):
    while not ctx.exit_event.is_set():
        input = await aioconsole.ainput()

        if ctx.input_requests > 0:
            ctx.input_requests -= 1
            ctx.input_queue.put_nowait(input)
            continue

        command = shlex.split(input)
        if not command:
            continue

        if command[0] == '/exit':
            ctx.exit_event.set()

        if command[0] == '/snes':
            ctx.snes_reconnect_address = None
            asyncio.create_task(snes_connect(ctx, command[1] if len(command) > 1 else ctx.snes_address))
        if command[0] in ['/snes_close', '/snes_quit']:
            ctx.snes_reconnect_address = None
            if ctx.snes_socket is not None and not ctx.snes_socket.closed:
                await ctx.snes_socket.close()

        if command[0] in ['/connect', '/reconnect']:
            ctx.server_address = None
            asyncio.create_task(connect(ctx, command[1] if len(command) > 1 else None))
        if command[0] == '/disconnect':
            ctx.server_address = None
            asyncio.create_task(disconnect(ctx))
        if command[0] == '/update':
            address = int(command[1], 16)
            value = int(command[2], 16)
            await send_msgs(ctx.socket, [['Update', address, value]])
        if command[0] == '/sync':
            await send_msgs(ctx.socket, [['Sync']])
        if command[0][:1] != '/':
            await send_msgs(ctx.socket, [['Say', input]])

        await snes_flush_writes(ctx)

async def track_ram(ctx : Context):
    # wait for snes connection!
    async with ctx.game_lock:
        rambuffer = await snes_read(ctx, WRAM_START + ctx.game.minram, (ctx.game.maxram - ctx.game.minram))

        def readRAM(address, size):
            return rambuffer[(address - ctx.game.minram):(address - ctx.game.minram + size)]

        def writeRAM(address, value):
            snes_buffered_write(ctx, WRAM_START + address, value)

        ctx.game.process(readRAM, writeRAM)
        await snes_flush_writes(ctx)

        updates = []
        for ram in ctx.game.locations:
            # logging.info("Checking " + ram.name)
            new_value = readRAM(ram.address, ram.size)
            if ram.value != new_value:
                # logging.info("ram.value = %s, new_value = %s" % (str(ram.value), str(new_value)))
                diff = ram.diff(new_value)
                ram.value = new_value
                ram.initialized = True
                # logging.info("new ram.value = %s" % str(ram.value))
                logging.info("Sending update: %s (%x) = %s" % (ram.name, ram.address, ram.format_diff(diff)))
                updates.append(['Update', [ram.address, diff]])

    await send_msgs(ctx.socket, updates)

async def game_watcher(ctx : Context):
    while not ctx.exit_event.is_set():
        try:
            await asyncio.wait_for(ctx.watcher_event.wait(), 2)
        except asyncio.TimeoutError:
            pass
        ctx.watcher_event.clear()

        gamemode = await snes_read(ctx, WRAM_START + 0x10, 1)
        if gamemode is None or gamemode[0] not in INGAME_MODES:
            continue

        if not ctx.synced.is_set():
          await send_msgs(ctx.socket, [['Sync']])

        await ctx.synced.wait()
        await track_ram(ctx)

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--snes', default='localhost:8080', help='Address of the QUsb2snes server.')
    parser.add_argument('--connect', default=None, help='Address of the multiworld host.')
    parser.add_argument('--password', default=None, help='Password of the multiworld host.')
    parser.add_argument('--loglevel', default='info', choices=['debug', 'info', 'warning', 'error', 'critical'])
    parser.add_argument('--name', default="dummy", help='Player name')
    args = parser.parse_args()

    logging.basicConfig(format='%(message)s', level=getattr(logging, args.loglevel.upper(), logging.INFO))

    ctx = Context(args.snes, args.connect, args.password)
    ctx.name = args.name

    input_task = asyncio.create_task(console_loop(ctx))

    await snes_connect(ctx, ctx.snes_address)

    if ctx.server_task is None:
        ctx.server_task = asyncio.create_task(server_loop(ctx))

    watcher_task = asyncio.create_task(game_watcher(ctx))


    await ctx.exit_event.wait()
    ctx.server_address = None
    ctx.snes_reconnect_address = None

    await watcher_task

    if ctx.socket is not None and not ctx.socket.closed:
        await ctx.socket.close()
    if ctx.server_task is not None:
        await ctx.server_task

    if ctx.snes_socket is not None and not ctx.snes_socket.closed:
        await ctx.snes_socket.close()

    while ctx.input_requests > 0:
        ctx.input_queue.put_nowait(None)
        ctx.input_requests -= 1

    await input_task

    await asyncio.gather(*asyncio.Task.all_tasks())

def exhandler(loop, context):
  print("ExceptiON!!!")
  print(str(context))

if __name__ == '__main__':
    colorama.init()
    asyncio.run(main())
    # loop = asyncio.get_event_loop()
    # loop.set_debug(True)
    # loop.set_exception_handler(exhandler)
    # loop.run_until_complete(main())
    # loop.run_until_complete(asyncio.gather(*asyncio.Task.all_tasks()))
    # loop.close()
    colorama.deinit()
