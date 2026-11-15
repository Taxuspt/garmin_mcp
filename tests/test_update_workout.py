import asyncio
from unittest.mock import Mock
from mcp.server.fastmcp import FastMCP
from garmin_mcp import workouts as w
client = Mock()
client.get_workout_by_id.return_value = {"workoutId": 123, "workoutName": "Alt"}
client.client.put.return_value = {}
w.configure(client); app = FastMCP("probe"); w.register_tools(app)
async def call(**kw):
    r = await app.call_tool("update_workout", kw); return r[1]["result"]
async def main():
    d = {"workoutName": "Neu", "workoutSegments": []}
    print("preview:", await call(workout_id=123, workout_data=d))
    print("confirm:", await call(workout_id=123, workout_data=d, confirm=True))
    print("put args:", client.client.put.call_args)
    print("id=0:", await call(workout_id=0, workout_data=d, confirm=True))
    client.client.put.side_effect = Exception("API Error 400 - bad")
    print("put fails:", await call(workout_id=123, workout_data=d, confirm=True))
    client.client.put.side_effect = None; client.client.put.reset_mock()
    bad = {"workoutName": "X", "workoutSegments": [{"workoutSteps": [{"type": "ExecutableStepDTO", "endCondition": {"conditionTypeId": 4, "conditionTypeKey": "heart.rate"}}]}]}
    print("invalid data:", await call(workout_id=123, workout_data=bad, confirm=True), "put calls:", client.client.put.call_count)
asyncio.run(main())
