"""HomeButler, rebuilt: the household's brain.

Home Assistant is the only device layer; this package holds the household's own data (to-dos, food,
schedules, members), the butler, notifications and the Dashboard's backend, and reaches devices only
through Home Assistant. See docs/refactor-plan.md.

Layers, each importing only those below it:
    api, jobs          entry points
    domain, assistant  household rules
    store              the database (the only code that touches it)
    integrations       outside services (the only code that touches them)
    settings           configuration
"""
