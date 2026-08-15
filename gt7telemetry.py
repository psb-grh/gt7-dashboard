"""Gran Turismo 7 telemetry packet decoder and parser.

GT7 broadcasts Salsa20-encrypted telemetry over UDP. To receive it, a client
must send a single-byte heartbeat ('A') to the PlayStation on port 33739; the
console then streams encrypted 296-byte packets back to the sender's port 33740.

The Salsa20 key, IV derivation and magic-number check follow the
reverse-engineered format documented by Nenkai (PDTools) and used by the
gt7dashboard project (https://github.com/snipem/gt7dashboard).
"""

import struct
from dataclasses import dataclass, asdict
from typing import Optional

from Crypto.Cipher import Salsa20

# Fixed Salsa20 key shared by all GT7 builds.
SALSA20_KEY = b"Simulator Interface Packet GT7 ver 0.0"

# Magic number present in every valid decrypted GT7 S0 packet.
MAGIC = 0x47375330


def salsa20_dec(data: bytes) -> bytearray:
    """Decrypt a raw GT7 telemetry packet.

    Returns an empty bytearray if the packet does not decode to the expected
    magic number (e.g. stray traffic or a malformed packet).
    """
    oiv = data[0x40:0x44]
    iv1 = int.from_bytes(oiv, byteorder="little")
    # Polyphony Digital uses DEADBEAF (sic), not DEADBEEF.
    iv2 = iv1 ^ 0xDEADBEAF
    iv = bytearray()
    iv.extend(iv2.to_bytes(4, "little"))
    iv.extend(iv1.to_bytes(4, "little"))

    cipher = Salsa20.new(SALSA20_KEY[0:32], bytes(iv))
    ddata = cipher.decrypt(data)

    if int.from_bytes(ddata[0:4], byteorder="little") != MAGIC:
        return bytearray()
    return ddata


@dataclass
class Telemetry:
    """The subset of telemetry fields the dashboard displays."""

    package_id: int = 0
    current_lap: int = 0
    total_laps: int = 0
    last_lap_ms: int = 0
    best_lap_ms: int = 0
    time_of_day_ms: int = 0
    car_speed: float = 0.0
    rpm: float = 0.0
    current_gear: int = 0
    throttle: float = 0.0
    brake: float = 0.0
    clutch: float = 0.0
    boost: float = 0.0
    oil_temp: float = 0.0
    water_temp: float = 0.0
    oil_pressure: float = 0.0
    fuel: float = 0.0
    fuel_capacity: float = 0.0
    in_race: bool = False
    is_paused: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def parse_packet(ddata: bytearray) -> Optional[Telemetry]:
    """Parse a decrypted GT7 packet into a Telemetry dataclass.

    Returns None if the buffer is too short to be a real packet.
    """
    if len(ddata) < 0x128:
        return None

    gear_byte = struct.unpack("B", ddata[0x90:0x91])[0]
    flags = struct.unpack("B", ddata[0x8E:0x8F])[0]

    return Telemetry(
        package_id=struct.unpack("i", ddata[0x70:0x74])[0],
        current_lap=struct.unpack("h", ddata[0x74:0x76])[0],
        total_laps=struct.unpack("h", ddata[0x76:0x78])[0],
        last_lap_ms=struct.unpack("i", ddata[0x7C:0x80])[0],
        best_lap_ms=struct.unpack("i", ddata[0x78:0x7C])[0],
        time_of_day_ms=struct.unpack("i", ddata[0x80:0x84])[0],
        car_speed=3.6 * struct.unpack("f", ddata[0x4C:0x50])[0],
        rpm=struct.unpack("f", ddata[0x3C:0x40])[0],
        current_gear=gear_byte & 0x0F,
        throttle=struct.unpack("B", ddata[0x91:0x92])[0] / 2.55,
        brake=struct.unpack("B", ddata[0x92:0x93])[0] / 2.55,
        clutch=struct.unpack("f", ddata[0xF4:0xF8])[0],
        boost=struct.unpack("f", ddata[0x50:0x54])[0] - 1.0,
        oil_temp=struct.unpack("f", ddata[0x5C:0x60])[0],
        water_temp=struct.unpack("f", ddata[0x58:0x5C])[0],
        oil_pressure=struct.unpack("f", ddata[0x54:0x58])[0],
        fuel=struct.unpack("f", ddata[0x44:0x48])[0],
        fuel_capacity=struct.unpack("f", ddata[0x48:0x4C])[0],
        in_race=bool(flags & 0x01),
        is_paused=bool(flags & 0x02),
    )
