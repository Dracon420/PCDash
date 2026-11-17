# HBB stream controller - boot.py (runs once at power-up, before code.py)
#
# Adds a second USB serial port ("data") next to the normal console. The PC
# dashboard writes OBS status to it so the key lights can follow the stream.
# The console port and the CIRCUITPY drive are left on, so the box can always
# be edited from Windows.
import usb_cdc

usb_cdc.enable(console=True, data=True)
