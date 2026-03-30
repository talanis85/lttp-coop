from functools import reduce
from game import *

class Alttp(Game):
  def __init__(self):
    super().__init__()

    self.locations = ramItems
    self.minram = 0x0
    self.maxram = 0xFFFF

  def process(self, readRAM, writeRAM):
    super().process(readRAM, writeRAM)

    # update keys
    dungeonMem = readRAM(0x040C, 1)
    dungeon = dungeonMem[0] // 2
    curKeysMem = readRAM(0xF36F, 1)
    curKeys = curKeysMem[0]

    if curKeys != 0xFF and dungeon <= 13:
      writeRAM(0xF37C + dungeon, bytes([curKeys]))

def set_bit(x, i):
  return x | (1 << i)

def unset_bit(x, i):
  return x & ~(1 << i)

def get_bit(x, i):
  return (x >> i) & 1

# 1 = flippers, 2 = boots
def receiveAbility(i):
  def f(readRAM, writeRAM, new, old):
    prevAbilityMem = readRAM(0xF379, 1)
    prevAbility = prevAbilityMem[0]
    if new:
      prevAbility = set_bit(prevAbility, i)
    else:
      prevAbility = unset_bit(prevAbility, i)
    writeRAM(0xF379, bytes([prevAbility]))
    return new

def receiveShovelFlute(readRAM, writeRAM, new, old):
  if new == 0 or (new != 0 and old == 0) or (new == 3 and new == 2):
    return new
  else:
    return old

def receiveSword(readRAM, writeRAM, new, old):
  if new > 0x80 or new < 0:
    return old
  else:
    return max(min(new, 4), 0)

def receiveTriforce(readRAM, writeRAM, new, old):
  if new == 0x19:
    return new
  else:
    return old

def receiveKey(address):
  def f(readRAM, writeRAM, new, old):
    if new >= 0xFF:
      return old

    dungeonMem = readRAM(0x040C, 1)
    dungeon = dungeonMem[0] / 2
    if dungeon == (address - 0xF37C):
      delta = new - old
      curKeysMem = readRAM(0xF36F, 1)
      curKeys = curKeysMem[0]
      curKeys = curKeys + delta
      writeRAM(0xF36F, bytes([curKeys]))

    return new

  return f

def zeroChange(readRAM, writeRAM, new, old):
  # this feels wrong?
  if new == 0 or (new != 0 and old == 0):
    return new
  else:
    return old

def clamp(a, b):
  def f(readRAM, writeRAM, new, old):
    new = max(new, a)
    new = min(new, b)
    return new

  return f

def receiveBottle(readRAM, writeRAM, new, old):
  return zeroChange(readRAM, writeRAM, new, old) 

def makeChestItem(i):
  a = RAM_Bits(0xF000 + i*2, names=[
    "Room %d quadrant #1" % i,
    "Room %d quadrant #2" % i,
    "Room %d quadrant #3" % i,
    "Room %d quadrant #4" % i,
    "Room %d chest #0" % i,
    "Room %d chest #1" % i,
    "Room %d chest #2" % i,
    "Room %d chest #3" % i
    ])

  b = RAM_Bits(0xF000 + i*2 + 1, names=[
    "Room %d chest #5 / rupee tile" % i,
    "Room %d chest #6 / key / item" % i,
    "Room %d key / item" % i,
    "Room %d boss dead" % i,
    "Room %d door #0" % i,
    "Room %d door #1" % i,
    "Room %d door #2" % i,
    "Room %d door #3" % i
    ])
  return [a,b]

ramItems = [
  # RAM_U8    (0x0010, name="Triforce Scene", onreceive=receiveTriforce),
  RAM_Bits  (0xF38C, names=["Bird", "Flute", "Shovel", "unknown item", "Magic Powder", "Mushroom", "Red Boomerang", "Blue Boomerang"]),
  RAM_Bits  (0xF38E, names=["unknown item", "unknown item", "unknown item", "unknown item", "unknown item", "unknown item", "Silver Arrows", "Bow"]),
  # RAM_U8    (0xF3C5, name="light world progress"), # needed to activate sword

  RAM_U8    (0xF340, name="Bows", onreceive=zeroChange),
  RAM_U8    (0xF341, name="Boomerangs", onreceive=zeroChange),

  RAM_Bool  (0xF342, name="Hookshot"),
  RAM_U8    (0xF344, name="Mushroom"),
  RAM_Bool  (0xF345, name="Fire Rod"),
  RAM_Bool  (0xF346, name="Ice Rod"),
  RAM_Bool  (0xF347, name="Bombos Medallion"),
  RAM_Bool  (0xF348, name="Ether Medallion"),
  RAM_Bool  (0xF349, name="Quake Medallion"),
  RAM_Bool  (0xF34A, name="Lantern"),
  RAM_Bool  (0xF34B, name="Magic Hammer"),
  RAM_U8    (0xF34C, name="Shovel/Flute", onreceive=receiveShovelFlute),
  RAM_Bool  (0xF34D, name="Bug Net"),
  RAM_Bool  (0xF34E, name="Book of Mudora"),
  RAM_U8    (0xF34F, name="Selected Bottle", onreceive=zeroChange),
  RAM_Bool  (0xF350, name="Cane of Somaria"),
  RAM_Bool  (0xF351, name="Cane of Byrna"),
  RAM_Bool  (0xF352, name="Magic Cape"),
  RAM_U8    (0xF353, name="Mirror"),
  RAM_U8    (0xF354, name="Glove"),
  RAM_Bool  (0xF355, name="Pegasus Boots", onreceive=receiveAbility(2)),
  RAM_Bool  (0xF356, name="Flippers", onreceive=receiveAbility(1)),
  RAM_Bool  (0xF357, name="Moon Pearl"),
  RAM_U8    (0xF359, name="Sword", onreceive=receiveSword),
  RAM_U8    (0xF416, name="Progressive Shield", onreceive=clamp(0, 0xC0)),
  RAM_U8    (0xF35A, name="Shield", onreceive=clamp(0, 3)),
  RAM_U8    (0xF35B, name="Armor", onreceive=clamp(0, 2)),
  RAM_U8    (0xF35C, name="Bottle #1", onreceive=receiveBottle),
  RAM_U8    (0xF35D, name="Bottle #2", onreceive=receiveBottle),
  RAM_U8    (0xF35E, name="Bottle #3", onreceive=receiveBottle),
  RAM_U8    (0xF35F, name="Bottle #4", onreceive=receiveBottle),
  RAM_Bits  (0xF364, names=["unused Compass", "unused Compass", "Ganon's Tower Compass", "Turtle Rock Compass", "Thieves Town Compass", "Tower of Hera Compass", "Ice Palace Compass", "Skull Woods Compass"]),
  RAM_Bits  (0xF365, names=["Misery Mire Compass", "Palace of Darkness Compass", "Swamp Palace Compass", "Agahnim's Tower Compass", "Desert Palace Compass", "Eastern Palace Compass", "Hyrule Castle Compass"]),
  RAM_Bits  (0xF366, names=["unused Boss Key", "unused Boss Key", "Ganon's Tower Boss Key", "Turtle Rock Boss Key", "Thieves Town Boss Key", "Tower of Hera Boss Key", "Ice Palace Boss Key", "Skull Woods Boss Key"]),
  RAM_Bits  (0xF367, names=["Misery Mire Boss Key", "Palace of Darkness Boss Key", "Swamp Palace Boss Key", "Agahnim's Tower Boss Key", "Desert Palace Boss Key", "Eastern Palace Boss Key", "Hyrule Castle Boss Key"]),
  RAM_Bits  (0xF368, names=["unused Map", "unused Map", "Ganon's Tower Map", "Turtle Rock Map", "Thieves Town Map", "Tower of Hera Map", "Ice Palace Map", "Skull Woods Map"]),
  RAM_Bits  (0xF369, names=["Misery Mire Map", "Palace of Darkness Map", "Swamp Palace Map", "Agahnim's Tower Map", "Desert Palace Map", "Eastern Palace Map", "Hyrule Castle Map"]),
  RAM_Bits  (0xF374, names=["Red Pendant", "Blue Pendant", "Green Pendant"]),
  RAM_Bits  (0xF37A, names=["Crystal 6", "Crystal 1", "Crystal 5", "Crystal 7", "Crystal 2", "Crystal 4", "Crystal 3", "unused"]),
  RAM_U8    (0xF37B, name="Magic upgrade", onreceive=clamp(0, 2)),

  # RAM_U8    (0xF36F, name="Key", onreceive=receiveKey(0xF36F)),
  RAM_U8    (0xF37C, name="Sewer Key", onreceive=receiveKey(0xF37C)),
  RAM_U8    (0xF37D, name="Hyrule Castle Key", onreceive=receiveKey(0xF37D)),
  RAM_U8    (0xF37E, name="Eastern Palace Key", onreceive=receiveKey(0xF37E)),
  RAM_U8    (0xF37F, name="Desert Palace Key", onreceive=receiveKey(0xF37F)),
  RAM_U8    (0xF380, name="Agahnim's Tower Key", onreceive=receiveKey(0xF380)),
  RAM_U8    (0xF381, name="Swamp Palace Key", onreceive=receiveKey(0xF381)),
  RAM_U8    (0xF382, name="Palace of Darkness Key", onreceive=receiveKey(0xF382)),
  RAM_U8    (0xF383, name="Misery Mire Key", onreceive=receiveKey(0xF383)),
  RAM_U8    (0xF384, name="Skull Woods Key", onreceive=receiveKey(0xF384)),
  RAM_U8    (0xF385, name="Ice Palace Key", onreceive=receiveKey(0xF385)),
  RAM_U8    (0xF386, name="Tower of Hera Key", onreceive=receiveKey(0xF386)),
  RAM_U8    (0xF387, name="Thieves Town Key", onreceive=receiveKey(0xF387)),
  RAM_U8    (0xF388, name="Turtle Rock Key", onreceive=receiveKey(0xF388)),
  RAM_U8    (0xF389, name="Ganon's Tower Key", onreceive=receiveKey(0xF389)),

	RAM_U16   (0xF360, name="Rupees", onreceive=clamp(0, 9999)),
  RAM_U8    (0xF36B, name="Heart Pieces", onreceive=lambda readRAM, writeRAM, new, old: new % 4),
	RAM_U8    (0xF36C, name="HP max", onreceive=clamp(0, 0xF0)),

	# item counts in dungeons. not really accurate because of chests that can be opened
	# multiple times. does not work at all with key drop shuffle.
	RAM_U8    (0xF434, name="Dungeon Item Count Byte 0"),
	RAM_U8    (0xF435, name="Dungeon Item Count Byte 1"),
	RAM_U8    (0xF436, name="Dungeon Item Count Byte 2"),
	RAM_U8    (0xF437, name="Dungeon Item Count Byte 3"),
	RAM_U8    (0xF438, name="Dungeon Item Count Byte 4"),
	RAM_U8    (0xF439, name="Dungeon Item Count Byte 5"),

  RAM_Bits  (0xF3C9, names=["Hobo gave bottle", "Vendor gave bottle", "Flute boy became tree", "Thief's chest opened", "Smith saved", "Smiths have your sword"])
  ] + reduce (lambda a, b: a + b, [makeChestItem(i) for i in range(0, 295)])

# inventory = [
#   0xF
# ]
