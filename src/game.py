import base64

def b64enc(x):
  return base64.b64encode(x).decode('ascii')

def b64dec(x):
  return base64.b64decode(x)

class Game:
  def __init__(self):
    self.locations = []

  def lookupRAM(self, address):
    for ram in self.locations:
      if ram.address == address:
        return ram
    return None

  def process(self, readRAM, writeRAM):
    pass

class RAM:
  def __init__(self, address, size=1, name=None, onreceive=None):
    self.value = bytes([0 for x in range(size)])
    self.initialized = False
    self.address = address
    self.size = size
    self.name = name
    self.onreceive = onreceive

  def diff(self, new):
    return b64enc(new)

  def apply_diff(self, diff):
    self.value = b64dec(diff)

  def format_diff(self, diff):
    return str(b64dec(diff))

  def format(self, x):
    return str(x)

  def from_bytes(x):
    return x

  def to_bytes(x):
    return x

class RAM_U8(RAM):
  def __init__(self, address, name, onreceive=None):
    super().__init__(address, 1, name=name, onreceive=onreceive)

  def from_bytes(self, b):
    return b[0]
  
  def to_bytes(self, x):
    return bytes([x])

class RAM_U16(RAM):
  def __init__(self, address, name, onreceive=None):
    super().__init__(address, 2, name=name, onreceive=onreceive)

  def from_bytes(self, b):
    return b[0] + b[1] * 256 # byte order?
  
  def to_bytes(self, x):
    lsb = x % 256
    msb = x // 256
    return bytes([lsb, msb])

class RAM_Bool(RAM_U8):
  def format(self, x):
    if x[0] == 0:
      return "N"
    else:
      return "Y"

  def format_diff(self, diff):
    x = b64dec(diff)
    if x[0] == 0:
      return "N"
    else:
      return "Y"

  def from_bytes(self, b):
    return b[0] != 0
  
  def to_bytes(self, x):
    return bytes([1 if x else 0])

def set_bit(x, i):
  return x | (1 << i)

def unset_bit(x, i):
  return x & ~(1 << i)

def get_bit(x, i):
  return (x >> i) & 1

class RAM_Bits(RAM):
  def __init__(self, address, names, onreceive=None):
    super().__init__(address, 1, name=hex(address), onreceive=onreceive)
    self.names = names
    self.value = bytes([0])

  def diff(self, new):
    out = []
    for i in range(8):
      if get_bit(self.value[0], i) != get_bit(new[0], i):
        out.append([i, get_bit(new[0], i)])
    return out

  def apply_diff(self, diff):
    x = self.value[0]
    for d in diff:
      if d[1] == 0:
        x = unset_bit(x, d[0])
      elif d[1] == 1:
        x = set_bit(x, d[0])
    self.value = bytes([x])

  def format_diff(self, diff):
    # return str(diff)
    s = ""
    for i in diff:
      if i[0] < len(self.names):
        s = s + "%s=%d " % (self.names[i[0]], i[1])
    return s

  # def diff(self, new):
  #   if self.value[0] == new[0]:
  #     return None
  #   else:
  #     return bytes([self.value[0] ^ new[0]])

  def format(self, x):
    s = ""
    i = 0
    for n in self.names:
      v = get_bit(self.value[0], i)
      s = s + "%s=%d " % (n, v)
      i = i + 1
    return s
