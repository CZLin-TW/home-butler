"""Home Assistant, the only device layer. HA connects out to this service over one WebSocket (it has no
public address): it sends what it shares (snapshots), and runs the few commands it allows.

Protocol 2 is protocol 1 plus each item's area and floor (the floor decides the household); both are
accepted while the integration is being updated.
"""
