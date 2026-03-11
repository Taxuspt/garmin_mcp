"""
Mock Garmin API response fixtures

These fixtures provide realistic sample data matching the actual Garmin Connect API responses.
Based on the python-garminconnect library response formats.
"""

# Activity Management
MOCK_ACTIVITIES = [
    {
        "activityId": 12345678901,
        "activityName": "Morning Run",
        "activityType": {"typeKey": "running", "typeId": 1},
        "startTimeLocal": "2024-01-15 07:00:00",
        "distance": 5000.0,
        "duration": 1800.0,
        "averageHR": 145,
        "maxHR": 165,
        "calories": 350,
        "averageSpeed": 2.78,
        "maxSpeed": 3.5
    },
    {
        "activityId": 12345678902,
        "activityName": "Cycling",
        "activityType": {"typeKey": "cycling", "typeId": 2},
        "startTimeLocal": "2024-01-14 16:00:00",
        "distance": 20000.0,
        "duration": 3600.0,
        "averageHR": 130,
        "maxHR": 155,
        "calories": 600
    }
]

MOCK_ACTIVITY_DETAILS = {
    "activityId": 12345678901,
    "activityName": "Morning Run",
    "activityType": {"typeKey": "running", "typeId": 1},
    "startTimeLocal": "2024-01-15 07:00:00",
    "distance": 5000.0,
    "duration": 1800.0,
    "averageHR": 145,
    "maxHR": 165,
    "calories": 350,
    "summaryDTO": {
        "totalDistance": 5000.0,
        "totalCalories": 350,
        "avgHR": 145,
        "maxHR": 165
    },
    "metadataDTO": {
        "deviceName": "Garmin Forerunner 945"
    }
}

MOCK_ACTIVITY_SPLITS = {
    "lapDTOs": [
        {
            "lapIndex": 1,
            "distance": 1000.0,
            "duration": 360.0,
            "averageHR": 142,
            "averageSpeed": 2.78,
            "elevationGain": 25.5,
            "elevationLoss": 10.2
        },
        {
            "lapIndex": 2,
            "distance": 1000.0,
            "duration": 350.0,
            "averageHR": 145,
            "averageSpeed": 2.86,
            "elevationGain": 15.0,
            "elevationLoss": 30.8
        }
    ]
}

# Workouts
MOCK_WORKOUTS = [
    {
        "workoutId": 123456,
        "workoutName": "5K Tempo Run",
        "sportType": {"sportTypeId": 1, "sportTypeKey": "running"},
        "workoutProvider": "GARMIN_COACH"
    }
]

MOCK_WORKOUT_DETAILS = {
    "workoutId": 123456,
    "workoutName": "5K Tempo Run",
    "description": "Tempo run workout for 5K training",
    "sportType": {"sportTypeId": 1, "sportTypeKey": "running"},
    "estimatedDuration": 2400,
    "estimatedDistance": 5000,
    "createdDate": "2024-01-15T10:00:00.0",
    "updatedDate": "2024-01-15T10:00:00.0",
    "workoutSegments": [
        {
            "segmentOrder": 1,
            "sportType": {"sportTypeId": 1, "sportTypeKey": "running"},
            "workoutSteps": [
                {
                    "stepId": 1001,
                    "stepOrder": 1,
                    "stepType": {"stepTypeId": 1, "stepTypeKey": "warmup"},
                    "description": "Easy warm up run",
                    "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"},
                    "endConditionValue": 600.0,
                    "targetType": {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"}
                },
                {
                    "stepId": 1002,
                    "stepOrder": 2,
                    "stepType": {"stepTypeId": 3, "stepTypeKey": "interval"},
                    "description": "Tempo pace",
                    "endCondition": {"conditionTypeId": 3, "conditionTypeKey": "distance"},
                    "endConditionValue": 5000.0,
                    "targetType": {"workoutTargetTypeId": 6, "workoutTargetTypeKey": "pace.zone"},
                    "zoneNumber": 4
                },
                {
                    "stepId": 1003,
                    "stepOrder": 3,
                    "stepType": {"stepTypeId": 2, "stepTypeKey": "cooldown"},
                    "description": "Cool down jog",
                    "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"},
                    "endConditionValue": 300.0,
                    "targetType": {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"}
                }
            ]
        }
    ]
}

# Activity Management - counts and types
MOCK_ACTIVITY_COUNT = 523

MOCK_ACTIVITY_TYPES = [
    {
        "typeId": 1,
        "typeKey": "running",
        "displayName": "Running",
        "parentTypeId": None,
        "isHidden": False,
    },
    {
        "typeId": 2,
        "typeKey": "cycling",
        "displayName": "Cycling",
        "parentTypeId": None,
        "isHidden": False,
    },
    {
        "typeId": 3,
        "typeKey": "hiking",
        "displayName": "Hiking",
        "parentTypeId": 17,
        "isHidden": False,
    },
    {
        "typeId": 163,
        "typeKey": "yoga",
        "displayName": "Yoga",
        "parentTypeId": 29,
        "isHidden": False,
    },
]
