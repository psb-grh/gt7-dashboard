# GT7 Telemetry Dashboard

Real-time telemetry dashboard for Gran Turismo 7, served from an Orange Pi.
Open http://192.168.0.9:8080/ on your phone.

## Install
pip3 install -r requirements.txt

## Run
python3 server.py
python3 server.py --ps-ip <PS5-IP>   # if broadcast doesn't find the PS5

## Requirements
- PS5 NAT type must be Type 2
- UDP ports 33740 (in) and 33739 (out) open on the Pi firewall
