from Ammeters.Circutor_Ammeter import CircutorAmmeter
from Ammeters.Entes_Ammeter import EntesAmmeter
from Ammeters.Greenlee_Ammeter import GreenleeAmmeter

# Emulator class for each ammeter name used in config.yaml. To emulate a new ammeter,
# write one small subclass of AmmeterEmulatorBase and add it here.
EMULATORS = {
    "greenlee": GreenleeAmmeter,
    "entes": EntesAmmeter,
    "circutor": CircutorAmmeter,
}
