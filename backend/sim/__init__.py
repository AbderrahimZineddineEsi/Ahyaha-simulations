"""
The simulation: seed data and scripted clients, as clients of the backend. Services never import this package (a test
enforces it), and with SOLVEX_DEMO=0 none of it exists.
"""


def install(app):
    from backend.sim import routes
    routes.mount(app)


async def start(app):
    from backend.sim.runtime import sim
    await sim.start(app)


async def stop():
    from backend.sim.runtime import sim
    await sim.stop()
