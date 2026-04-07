"""
Workout template resources for Garmin MCP Server

Provides MCP resources with valid workout JSON structures that clients can
read and use as templates for creating custom workouts via upload_workout.
"""
import json

# =============================================================================
# WORKOUT TEMPLATES
# =============================================================================

SIMPLE_RUN_TEMPLATE = {
    "workoutName": "Simple Run",
    "description": "Basic run workout: warmup, run, cooldown",
    "sportType": {"sportTypeId": 1, "sportTypeKey": "running"},
    "workoutSegments": [{
        "segmentOrder": 1,
        "sportType": {"sportTypeId": 1, "sportTypeKey": "running"},
        "workoutSteps": [
            {
                "type": "ExecutableStepDTO",
                "stepOrder": 1,
                "stepType": {"stepTypeId": 1, "stepTypeKey": "warmup"},
                "description": "Warmup 5 min",
                "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"},
                "endConditionValue": 300.0,
                "targetType": {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"}
            },
            {
                "type": "ExecutableStepDTO",
                "stepOrder": 2,
                "stepType": {"stepTypeId": 3, "stepTypeKey": "interval"},
                "description": "Run 20 min",
                "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"},
                "endConditionValue": 1200.0,
                "targetType": {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"}
            },
            {
                "type": "ExecutableStepDTO",
                "stepOrder": 3,
                "stepType": {"stepTypeId": 2, "stepTypeKey": "cooldown"},
                "description": "Cooldown 5 min",
                "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"},
                "endConditionValue": 300.0,
                "targetType": {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"}
            }
        ]
    }]
}

INTERVAL_RUNNING_TEMPLATE = {
    "workoutName": "Interval Run",
    "description": "Interval workout with repeat groups: warmup, 6x(400m fast + 2min recovery), cooldown",
    "sportType": {"sportTypeId": 1, "sportTypeKey": "running"},
    "workoutSegments": [{
        "segmentOrder": 1,
        "sportType": {"sportTypeId": 1, "sportTypeKey": "running"},
        "workoutSteps": [
            {
                "type": "ExecutableStepDTO",
                "stepOrder": 1,
                "stepType": {"stepTypeId": 1, "stepTypeKey": "warmup"},
                "description": "Warmup 10 min",
                "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"},
                "endConditionValue": 600.0,
                "targetType": {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"}
            },
            {
                "type": "RepeatGroupDTO",
                "stepOrder": 2,
                "numberOfIterations": 6,
                "workoutSteps": [
                    {
                        "type": "ExecutableStepDTO",
                        "stepOrder": 1,
                        "stepType": {"stepTypeId": 3, "stepTypeKey": "interval"},
                        "description": "Fast 400m",
                        "endCondition": {"conditionTypeId": 3, "conditionTypeKey": "distance"},
                        "endConditionValue": 400.0,
                        "targetType": {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"}
                    },
                    {
                        "type": "ExecutableStepDTO",
                        "stepOrder": 2,
                        "stepType": {"stepTypeId": 4, "stepTypeKey": "recovery"},
                        "description": "Recovery 2 min",
                        "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"},
                        "endConditionValue": 120.0,
                        "targetType": {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"}
                    }
                ]
            },
            {
                "type": "ExecutableStepDTO",
                "stepOrder": 3,
                "stepType": {"stepTypeId": 2, "stepTypeKey": "cooldown"},
                "description": "Cooldown 10 min",
                "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"},
                "endConditionValue": 600.0,
                "targetType": {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"}
            }
        ]
    }]
}

TEMPO_RUN_TEMPLATE = {
    "workoutName": "Tempo Run",
    "description": "Tempo workout: warmup, 20min at tempo pace (HR zone 4), cooldown",
    "sportType": {"sportTypeId": 1, "sportTypeKey": "running"},
    "workoutSegments": [{
        "segmentOrder": 1,
        "sportType": {"sportTypeId": 1, "sportTypeKey": "running"},
        "workoutSteps": [
            {
                "type": "ExecutableStepDTO",
                "stepOrder": 1,
                "stepType": {"stepTypeId": 1, "stepTypeKey": "warmup"},
                "description": "Warmup 10 min",
                "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"},
                "endConditionValue": 600.0,
                "targetType": {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"}
            },
            {
                "type": "ExecutableStepDTO",
                "stepOrder": 2,
                "stepType": {"stepTypeId": 3, "stepTypeKey": "interval"},
                "description": "Tempo 20 min - HR Zone 4",
                "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"},
                "endConditionValue": 1200.0,
                "targetType": {"workoutTargetTypeId": 4, "workoutTargetTypeKey": "heart.rate.zone"},
                "zoneNumber": 4
            },
            {
                "type": "ExecutableStepDTO",
                "stepOrder": 3,
                "stepType": {"stepTypeId": 2, "stepTypeKey": "cooldown"},
                "description": "Cooldown 10 min",
                "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"},
                "endConditionValue": 600.0,
                "targetType": {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"}
            }
        ]
    }]
}

STRENGTH_CIRCUIT_TEMPLATE = {
    "workoutName": "Strength Pilot",
    "description": "Strength workout with Garmin exercise metadata: warmup, 3x air squat + rest, cooldown",
    "sportType": {"sportTypeId": 5, "sportTypeKey": "strength_training", "displayOrder": 5},
    "workoutSegments": [{
        "segmentOrder": 1,
        "sportType": {"sportTypeId": 5, "sportTypeKey": "strength_training", "displayOrder": 5},
        "workoutSteps": [
            {
                "type": "ExecutableStepDTO",
                "stepOrder": 1,
                "stepType": {"stepTypeId": 1, "stepTypeKey": "warmup"},
                "description": "Warmup",
                "endCondition": {"conditionTypeId": 1, "conditionTypeKey": "lap.button", "displayOrder": 1, "displayable": True},
                "targetType": {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"}
            },
            {
                "type": "RepeatGroupDTO",
                "stepOrder": 2,
                "numberOfIterations": 3,
                "stepType": {"stepTypeId": 6, "stepTypeKey": "repeat", "displayOrder": 6},
                "endCondition": {"conditionTypeId": 7, "conditionTypeKey": "iterations", "displayOrder": 7, "displayable": False},
                "endConditionValue": 3.0,
                "workoutSteps": [
                    {
                        "type": "ExecutableStepDTO",
                        "stepOrder": 1,
                        "stepType": {"stepTypeId": 3, "stepTypeKey": "interval"},
                        "description": "Air squat",
                        "endCondition": {"conditionTypeId": 10, "conditionTypeKey": "reps", "displayOrder": 10, "displayable": True},
                        "endConditionValue": 12.0,
                        "targetType": {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"},
                        "category": "SQUAT",
                        "exerciseName": "AIR_SQUAT"
                    },
                    {
                        "type": "ExecutableStepDTO",
                        "stepOrder": 2,
                        "stepType": {"stepTypeId": 5, "stepTypeKey": "rest"},
                        "description": "Rest",
                        "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time", "displayOrder": 2, "displayable": True},
                        "endConditionValue": 90.0,
                        "targetType": {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"}
                    }
                ]
            },
            {
                "type": "ExecutableStepDTO",
                "stepOrder": 3,
                "stepType": {"stepTypeId": 2, "stepTypeKey": "cooldown"},
                "description": "Cooldown",
                "endCondition": {"conditionTypeId": 1, "conditionTypeKey": "lap.button", "displayOrder": 1, "displayable": True},
                "targetType": {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"}
            }
        ]
    }]
}

# Reference documentation for workout structure
WORKOUT_STRUCTURE_REFERENCE = {
    "description": "Reference guide for Garmin workout JSON structure",
    "step_types": {
        "ExecutableStepDTO": "Regular workout step (warmup, interval, cooldown, recovery, rest)",
        "RepeatGroupDTO": "Repeat group containing nested steps with numberOfIterations"
    },
    "stepType_values": {
        "1": {"stepTypeKey": "warmup", "description": "Warmup phase"},
        "2": {"stepTypeKey": "cooldown", "description": "Cooldown phase"},
        "3": {"stepTypeKey": "interval", "description": "Work/effort interval"},
        "4": {"stepTypeKey": "recovery", "description": "Recovery between intervals"},
        "5": {"stepTypeKey": "rest", "description": "Complete rest"}
    },
    "endCondition_values": {
        "1": {"conditionTypeKey": "lap.button", "description": "Manual lap press"},
        "2": {"conditionTypeKey": "time", "description": "Duration in seconds"},
        "3": {"conditionTypeKey": "distance", "description": "Distance in meters"},
        "10": {"conditionTypeKey": "reps", "description": "Number of repetitions"}
    },
    "targetType_values": {
        "1": {"workoutTargetTypeKey": "no.target", "description": "No specific target"},
        "4": {"workoutTargetTypeKey": "heart.rate.zone", "description": "Heart rate zone (use zoneNumber 1-5)"},
        "6": {"workoutTargetTypeKey": "pace.zone", "description": "Pace zone (use zoneNumber)"}
    },
    "sportType_values": {
        "1": {"sportTypeKey": "running"},
        "2": {"sportTypeKey": "cycling"},
        "5": {"sportTypeKey": "strength_training"},
        "11": {"sportTypeKey": "walking"}
    }
}


def register_resources(app):
    """Register workout template resources with the MCP server app"""

    @app.resource("workout://templates/simple-run")
    async def get_simple_run_template() -> str:
        """Simple run workout template (warmup, run, cooldown)

        A basic running workout structure suitable for easy runs.
        Modify the endConditionValue to adjust durations.
        """
        return json.dumps(SIMPLE_RUN_TEMPLATE, indent=2)

    @app.resource("workout://templates/interval-running")
    async def get_interval_template() -> str:
        """Interval running workout template with repeat groups

        Demonstrates RepeatGroupDTO for interval training.
        Includes 6x400m intervals with 2min recovery.
        """
        return json.dumps(INTERVAL_RUNNING_TEMPLATE, indent=2)

    @app.resource("workout://templates/tempo-run")
    async def get_tempo_template() -> str:
        """Tempo run workout template with heart rate zone target

        Demonstrates targeting a specific heart rate zone.
        20min tempo block at HR zone 4.
        """
        return json.dumps(TEMPO_RUN_TEMPLATE, indent=2)

    @app.resource("workout://templates/strength-circuit")
    async def get_strength_template() -> str:
        """Strength training circuit template

        Garmin-compatible strength workout template with a real exercise.
        Demonstrates the required category + exerciseName + reps shape.
        """
        return json.dumps(STRENGTH_CIRCUIT_TEMPLATE, indent=2)

    @app.resource("workout://reference/structure")
    async def get_structure_reference() -> str:
        """Reference guide for workout JSON structure

        Documents valid values for step types, conditions, targets, and sports.
        Use this to understand what values are valid in workout definitions.
        """
        return json.dumps(WORKOUT_STRUCTURE_REFERENCE, indent=2)

    return app
