"""Serial transport: poll one JK-PB pack over the shared RS485 bus.

Single master on the bus (the Raspberry Pi). Polls each DIP address in turn,
honouring COMMAND_GAP between transactions.
"""

import time
from typing import Optional

import serial  # pyserial

from . import protocol as P


class JkPbBus:
    def __init__(self, port: str, baud: int = P.BAUD, timeout: float = 0.4):
        self.port = port
        self.baud = baud
        self.timeout = timeout
        self._ser: Optional[serial.Serial] = None

    def open(self):
        self._ser = serial.Serial(
            self.port, baudrate=self.baud, timeout=self.timeout,
            bytesize=serial.EIGHTBITS, parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
        )

    def close(self):
        if self._ser and self._ser.is_open:
            self._ser.close()

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *exc):
        self.close()

    def poll(self, address: int, cell_count: int = 16) -> Optional[P.PackReading]:
        """Send status request to one address, read + decode the reply.

        Returns None on timeout / bad frame (caller decides retry/skip).
        """
        assert self._ser is not None, "bus not open"
        req = P.build_request(address, P.CMD_STATUS)

        self._ser.reset_input_buffer()
        self._ser.write(req)
        self._ser.flush()

        # read enough for one full frame (+ slack for ACK / preamble)
        raw = self._ser.read(P.FRAME_LEN + 64)
        frame = P.find_frame(raw)
        if frame is None or not P._valid_checksum(frame):
            return None
        return P.decode_status(frame, address, cell_count)

    def poll_all(self, addresses, cell_count: int = 16):
        """Poll every address once. Yields (address, PackReading|None)."""
        for addr in addresses:
            yield addr, self.poll(addr, cell_count)
            time.sleep(P.COMMAND_GAP)
