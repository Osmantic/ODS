"""Initialize a new private volume once; never repair or erase existing state."""
import os
import runpy

from transition_gate import initialize


def provision_empty_volume(path):
    if os.listdir(path):
        return  # Existing and interrupted stores belong to the durable gate.
    initialize(path)


if __name__ == "__main__":
    provision_empty_volume(os.environ["PIXEL_TRANSITION_STATE_DIR"])
    runpy.run_module("pixel_edge", run_name="__main__")
