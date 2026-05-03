# Safety and testing

Free Motion connects AI and automation to **drones and robots**. Treat every change as if it could command real mass, blades, or motors.

This is a **minimum** baseline for anyone running experiments. Read it before your first on-device test.

## General rules

- **No surprises.** Anyone near the machine should know when a test starts and stops.
- **One person drives.** Multiple simultaneous controllers are how honest mistakes become incidents.
- **Assume bugs.** Software will mis-detect, mis-parse, or repeat the wrong command.
- **Incremental exposure.** Prove the safe path before you add motion, altitude, or speed.

## Before you power anything that can move

1. **Remove or constrain energy**  
   - Props off or blocked for bench tests when testing motor outputs.  
   - Robot on blocks or with drive wheels unloaded when validating motor directions.  
   - Use current-limited supplies where practical.

2. **Define a kill path**  
   - Physical: unplug, disconnect battery, accessible E-stop if you have one.  
   - Software: a clear “disarm / idle / stop” you can trigger without the main stack (for example a known safe script or RC override if applicable).

3. **Bound the test**  
   - Small area, clear floor, no spectators inside the arc of motion.  
   - Cord range, ceiling height, and obstacle spacing noted before spin-up.

4. **Dry run the logic**  
   - Log commands only (no actuators) until the chain OpenClaw → Telegram → device behaves predictably.

5. **Rate limits and timeouts**  
   - Prefer hard caps on command rate, duration, and maximum deflection. Fail closed to “safe idle” on lost link or parse errors.

## When something goes wrong

- Kill power first, debug second.
- After an uncommanded motion, do not declare “fixed” until you can explain the failure mode and how the test setup prevented injury.

## Compliance and responsibility

Laws and regulations for drones, airspace, and autonomous systems vary by country and use case. **You** are responsible for following local rules and for operating in conditions your insurance and ethics allow.

This document is not legal advice and not a substitute for manufacturer documentation, flight manuals, or site-specific risk assessments.

## Contributing

Improvements to this file (checklists, diagrams, platform-specific notes) are welcome. If you add a feature that can move hardware, add or update the test and safety notes needed to use it responsibly.
