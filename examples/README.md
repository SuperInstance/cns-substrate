# Examples — Living Patterns

Three runnable scripts showing the core communication patterns of the CNS bus.

## [lucineer_agent.py](lucineer_agent.py) — Request-Response

Lucineer sends a QUERY to Hermes and waits for a response via `HeartbeatPoller`. The simplest pattern: one agent asks, the bus carries, the response arrives.

```python
lucineer.send(
    intent=Intent.QUERY,
    message="Hermes, what is the current fleet status?",
)
```

## [wesley_agent.py](wesley_agent.py) — Fire-and-Forget

Wesley sends night-school training results to Hermes. No response expected. The ensign reports and returns to his quarters. This is how the overnight watch works — [Wesley](https://github.com/SuperInstance/wesley-journal) (dead) learns, records, and the bus carries it home.

## [fleet_broadcast.py](fleet_broadcast.py) — Many-to-Many

Lucineer broadcasts a roll call. Multiple specialists (Wesley, KimiCode, DeepSeek) respond by writing directly to the coordinator's inbox. This mirrors the overnight watch pattern where Lucineer checks on all crew members.

```
[Lucineer] Roll call sent
[Wesley]   Report sent
[KimiCode] Report sent
[DeepSeek] Report sent
[Lucineer] Received 3 responses
```

---

These examples use temporary directories so they can run anywhere without the Windows Hermes mount. In production, the default paths connect to the real bus.

← Back to **[CNS Bridge](../README.md)**
