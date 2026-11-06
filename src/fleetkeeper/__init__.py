"""fleetkeeper -- evidence-based guardian for local AI service fleets.

A local AI stack (gateways, Ollama, llama.cpp servers, job runners) dies in
ways generic tools do not cover: a port is alive but the service is a zombie,
a process is running but stopped doing work, a self-heal loop fights a VPN
outage and spawns a dozen zombies. fleetkeeper encodes the counter-measures
as small, pure-stdlib modules with an evidence ladder (L0..L4):

    L0 config exists -> L1 process -> L2 port -> L3 protocol -> L4 business
"""
__version__ = "0.1.1"
