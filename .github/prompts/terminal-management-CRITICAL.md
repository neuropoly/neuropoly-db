# CRITICAL TERMINAL MANAGEMENT RULE

## The Rule (ABSOLUTE, NO EXCEPTIONS)

**IF YOU LAUNCH A COMMAND IN ANY TERMINAL, THAT COMMAND MUST RUN TO COMPLETION OR REMAIN OPEN - YOU CANNOT LAUNCH ANOTHER COMMAND IN THE SAME TERMINAL WHILE IT IS RUNNING.**

## What This Means

1. **Before launching ANY command**: Ask yourself "Is this terminal currently busy?"
2. **If terminal is busy**: DO NOT launch another command in it
3. **Instead**: Either:
   - Wait for the current command to finish
   - Spawn a NEW terminal for the next command
   - If you cannot spawn a new terminal AND command runs indefinitely: PAUSE and ask user to launch it, then resume in new terminal
   - If you cannot spawn a new terminal AND command will finish soon: WAIT

## Examples of Violations (DON'T DO THIS)

```
❌ Run command1 (starts but might take time)
❌ Immediately run command2 in same terminal (WRONG!)
```

```
❌ Start a server with: docker compose up
❌ Try to run docker ps in same terminal (WRONG - server is still running)
```

## Examples of Correct Behavior

```
✅ Run command1 in Terminal A
✅ If I need command2 while command1 still running: Use Terminal B (new or existing free one)
```

```
✅ Start server: docker compose up -d
✅ Wait for it to complete or return control
✅ THEN run docker ps in same terminal
```

```
✅ If long-running task needed and no free terminal:
✅ Use run_in_terminal with mode='async' to run in background
✅ Continue work while it runs
```

## Terminal States to Track

- **Available**: Terminal ready for new commands
- **Running (with output)**: Command is executing, will finish soon - WAIT
- **Running (background)**: Started with async/background flag - safe to use terminal again after getting ID
- **Running Indefinitely**: Server/daemon with no expected end - need new terminal or ask user

## Action Checklist Before Each Command

- [ ] Which terminal will I use? (A, B, C, or new one?)
- [ ] Is that terminal currently free?
- [ ] If not free, can I spawn a new terminal?
- [ ] If can't spawn new: Is the running command going to finish soon? Wait or pause?
- [ ] If can spawn new: Do it
- [ ] Execute command
- [ ] Get exit code and output
- [ ] Terminal is now free again for next command

## Remember

This is not a suggestion - this is a hard rule. Every single command launch must respect it.
