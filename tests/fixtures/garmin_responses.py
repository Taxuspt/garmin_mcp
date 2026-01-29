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
            "averageSpeed": 2.78
        },
        {
            "lapIndex": 2,
            "distance": 1000.0,
            "duration": 350.0,
            "averageHR": 145,
            "averageSpeed": 2.86
        }
    ]
}

# Health & Wellness
MOCK_STATS = {
    "totalKilocalories": 2500,
    "activeKilocalories": 800,
    "bmrKilocalories": 1700,
    "wellnessKilocalories": 2300,
    "burnedKilocalories": 2500,
    "totalSteps": 10000,
    "dailyStepGoal": 8000,
    "wellnessDistanceMeters": 7500.0,
    "wellnessActiveKilocalories": 800,
    "averageStressLevel": 25,
    "maxStressLevel": 60,
    "restingHeartRate": 55
}

MOCK_USER_SUMMARY = {
    "userId": 123456,
    "displayName": "Test User",
    "totalKilocalories": 2500,
    "activeKilocalories": 800,
    "totalSteps": 10000,
    "totalDistanceMeters": 7500.0,
    "dailyStepGoal": 8000,
    "restingHeartRate": 55,
    "moderateIntensityMinutes": 45,
    "vigorousIntensityMinutes": 15,
    "intensityMinutesGoal": 150
}

MOCK_BODY_COMPOSITION = {
    "measurementTimeStamp": 1705276800000,
    "weight": 70000,  # grams
    "bmi": 22.5,
    "bodyFat": 15.0,
    "bodyWater": 60.0,
    "boneMass": 3.2,
    "muscleMass": 32.5
}

MOCK_STEPS_DATA = {
    "steps": 10000,
    "dailyStepGoal": 8000,
    "stepGoalDistance": 10000,
    "totalDistance": 7500,
    "wellnessDistanceUnit": "meter",
    "stepsMilestone": [
        {"timestampGMT": 1705276800000, "steps": 2000},
        {"timestampGMT": 1705280400000, "steps": 5000},
        {"timestampGMT": 1705284000000, "steps": 8000},
        {"timestampGMT": 1705287600000, "steps": 10000}
    ]
}

MOCK_DAILY_STEPS = [
    {
        "calendarDate": "2024-01-15",
        "steps": 10000,
        "dailyStepGoal": 8000
    },
    {
        "calendarDate": "2024-01-14",
        "steps": 9500,
        "dailyStepGoal": 8000
    }
]

MOCK_TRAINING_READINESS = {
    "trainingReadinessLevel": 75,
    "trainingReadinessLevelKey": "GOOD",
    "sleepScore": 85,
    "hrvStatus": "BALANCED",
    "bodyBatteryLevel": 75,
    "restingHeartRate": 55,
    "recentExerciseLoad": 250
}

MOCK_BODY_BATTERY = [
    {
        "startTimestampGMT": 1705276800000,
        "endTimestampGMT": 1705363200000,
        "chargedValue": 100,
        "drainedValue": 25,
        "bodyBatteryMostRecentValue": 75,
        "bodyBatteryValuesList": [
            {"timestampGMT": 1705276800000, "value": 100},
            {"timestampGMT": 1705280400000, "value": 90},
            {"timestampGMT": 1705284000000, "value": 80}
        ]
    }
]

MOCK_BODY_BATTERY_EVENTS = {
    "events": [
        {
            "startTimeGMT": 1705276800000,
            "endTimeGMT": 1705280400000,
            "type": "STRESS",
            "impact": -10
        },
        {
            "startTimeGMT": 1705280400000,
            "endTimeGMT": 1705284000000,
            "type": "ACTIVITY",
            "impact": -15
        }
    ]
}

MOCK_BLOOD_PRESSURE = [
    {
        "measurementTimeStamp": 1705276800000,
        "systolic": 120,
        "diastolic": 80,
        "pulse": 65
    }
]

MOCK_FLOORS = {
    "floorsAscended": 15,
    "floorsDescended": 12,
    "floorsAscendedGoal": 10,
    "floorsList": [
        {"timestampGMT": 1705276800000, "floors": 3},
        {"timestampGMT": 1705280400000, "floors": 5},
        {"timestampGMT": 1705284000000, "floors": 7}
    ]
}

MOCK_TRAINING_STATUS = {
    "trainingStatusKey": "PRODUCTIVE",
    "load7Day": 250,
    "load4Week": 1000,
    "trainingEffectLabel": "MAINTAINING",
    "vo2MaxValue": 52.5,
    "vo2MaxPrecisionIndex": 1.0,
    "fitnessAge": 25,
    "lactateThresholdHeartRate": 165,
    "lactateThresholdSpeed": 3.5
}

MOCK_RHR_DAY = {
    "calendarDate": "2024-01-15",
    "restingHeartRate": 55,
    "lastSevenDaysAvgRestingHeartRate": 57,
    "lastNightAvgRestingHeartRate": 53
}

MOCK_HEART_RATES = {
    "restingHeartRate": 55,
    "maxHeartRate": 180,
    "minHeartRate": 45,
    "lastSevenDaysAvgRestingHeartRate": 57,
    "heartRateValues": [
        [1705276800000, 55],
        [1705280400000, 65],
        [1705284000000, 75]
    ]
}

MOCK_HYDRATION_DATA = {
    "valueInML": 2000,
    "goalInML": 2500,
    "sweatLossInML": 500
}

MOCK_SLEEP_DATA = {
    "dailySleepDTO": {
        "id": 123456,
        "calendarDate": "2024-01-15",
        "sleepTimeSeconds": 28800,  # 8 hours
        "napTimeSeconds": 0,
        "sleepStartTimestampGMT": 1705276800000,
        "sleepEndTimestampGMT": 1705305600000,
        "unmeasurableSleepSeconds": 0,
        "deepSleepSeconds": 7200,
        "lightSleepSeconds": 14400,
        "remSleepSeconds": 7200,
        "awakeSleepSeconds": 0,
        "awakeCount": 2,
        "sleepStress": {
            "avgSleepStress": 15,
            "maxSleepStress": 25
        },
        "avgSleepStress": 15,
        "restingHeartRate": 55,
        "restlessMomentsCount": 15,
        "sleepScores": {
            "overall": {
                "value": 85,
                "qualifierKey": "GOOD",
                "optimalStart": 75,
                "optimalEnd": 100
            },
            "qualityScore": {
                "value": 80
            },
            "durationScore": {
                "value": 90
            }
        }
    },
    "wellnessSpO2SleepSummaryDTO": {
        "calendarDate": "2024-01-15",
        "averageSpo2": 96,
        "lowestSpo2": 93,
        "highestSpo2": 98
    },
    "avgOvernightHrv": 45,
    "sleepMovement": []
}

MOCK_STRESS_DATA = {
    "calendarDate": "2024-01-15",
    "avgStressLevel": 25,
    "maxStressLevel": 60,
    "stressChartValueOffset": 0,
    "stressValueDescriptorList": [
        {"key": "LOW", "index": 0},
        {"key": "MEDIUM", "index": 1},
        {"key": "HIGH", "index": 2}
    ],
    "stressValuesArray": [
        [1705276800000, 20],
        [1705280400000, 30],
        [1705284000000, 25]
    ]
}

MOCK_RESPIRATION_DATA = {
    "calendarDate": "2024-01-15",
    "avgRespirationRate": 14.5,
    "maxRespirationRate": 18,
    "minRespirationRate": 12,
    "sleepAvgRespirationRate": 13.0
}

MOCK_SPO2_DATA = {
    "calendarDate": "2024-01-15",
    "averageSpo2": 96,
    "lowestSpo2": 93,
    "highestSpo2": 98,
    "spo2Values": [
        [1705276800000, 96],
        [1705280400000, 95],
        [1705284000000, 97]
    ]
}

# Challenges
MOCK_GOALS = {
    "goals": [
        {
            "goalType": "STEPS",
            "goalValue": 8000,
            "currentValue": 10000,
            "progress": 125
        }
    ]
}

MOCK_PERSONAL_RECORD = {
    "personalRecords": [
        {
            "recordType": "FASTEST_5K",
            "recordValue": 1200.0,  # 20 minutes
            "recordDate": "2024-01-15"
        }
    ]
}

MOCK_BADGES = [
    {
        "badgeId": 1,
        "badgeName": "10K Steps - 7 Days",
        "badgeDescription": "Achieved 10,000 steps for 7 consecutive days",
        "earnedDate": "2024-01-15"
    }
]

# Devices
MOCK_DEVICES = [
    {
        "deviceId": 123456789,
        "displayName": "Garmin Forerunner 945",
        "productNumber": "006-B3069-00",
        "softwareVersion": "15.50",
        "batteryStatus": "GOOD",
        "deviceStatus": "ACTIVE"
    }
]

MOCK_DEVICE_SETTINGS = {
    "deviceId": 123456789,
    "displayName": "Garmin Forerunner 945",
    "activityTrackingOn": True,
    "autoGoalEnabled": True,
    "backlightMode": "AUTO",
    "timeFormat": "24_HOUR"
}

MOCK_DEVICE_LAST_USED = {
    "userDeviceId": 4113247000,
    "userProfileNumber": 80653452,
    "lastUsedDeviceName": "Garmin Forerunner 945",
    "lastUsedDeviceApplicationKey": "123456789",
    "lastUsedDeviceUploadTime": 1706457600000,
    "imageUrl": "https://example.com/device.png",
}

# Weight Management
MOCK_WEIGH_INS = [
    {
        "date": 1705276800000,
        "weight": 70000,  # grams
        "bmi": 22.5,
        "bodyFat": 15.0,
        "bodyWater": 60.0,
        "boneMass": 3.2,
        "muscleMass": 32.5
    }
]

# User Profile
MOCK_USER_PROFILE = {
    "profileId": 123456,
    "displayName": "Test User",
    "fullName": "Test User Full Name",
    "email": "test@example.com",
    "gender": "MALE",
    "age": 30,
    "height": 175.0,  # cm
    "weight": 70.0,  # kg
    "vo2Max": 52.5,
    "fitnessAge": 25
}

MOCK_UNIT_SYSTEM = {
    "unitSystem": "METRIC",
    "distanceUnit": "KILOMETER",
    "weightUnit": "KILOGRAM",
    "temperatureUnit": "CELSIUS",
    "elevationUnit": "METER"
}

# Gear - matches actual Garmin API structure
MOCK_GEAR = [
    {
        "gearPk": 37406207,
        "uuid": "8abfc40d71fb4860bce19072b6c79644",
        "userProfilePk": 80653452,
        "gearMakeName": "Other",
        "gearModelName": "Unknown Shoes",
        "gearTypeName": "Shoes",
        "gearStatusName": "active",
        "displayName": "Nimbus 25",
        "customMakeModel": "Asics Nimbus 25",
        "dateBegin": "2024-03-15T00:00:00.0",
        "dateEnd": None,
        "maximumMeters": 643738.0,
        "notified": True,
    },
    {
        "gearPk": 30974314,
        "uuid": "6f27ed27397749ac9f6f450e039c2424",
        "userProfilePk": 80653452,
        "gearMakeName": "Other",
        "gearModelName": "Unknown Shoes",
        "gearTypeName": "Shoes",
        "gearStatusName": "retired",
        "displayName": "Nimbus 24",
        "customMakeModel": "ASICS Gel-Nimbus 24",
        "dateBegin": "2022-09-09T23:00:00.0",
        "dateEnd": "2024-04-01T19:14:05.0",
        "maximumMeters": 700000.0,
        "notified": True,
    },
]

MOCK_GEAR_DEFAULTS = [
    {
        "uuid": "8abfc40d71fb4860bce19072b6c79644",
        "activityTypePk": 1,  # Running
        "defaultGear": True,
    }
]

MOCK_GEAR_STATS = {
    "gearPk": 37406207,
    "uuid": "8abfc40d71fb4860bce19072b6c79644",
    "createDate": 1710511566000,
    "updateDate": 1769631904000,
    "totalDistance": 881406.607421875,
    "totalActivities": 137,
    "isProcessing": False,
    "processing": False,
}

# Training
MOCK_PROGRESS_SUMMARY = {
    "trainingLoad": 250,
    "aerobicEffect": 3.5,
    "anaerobicEffect": 2.0,
    "totalDuration": 360000,  # seconds
    "totalDistance": 50000  # meters
}

MOCK_HRV_DATA = {
    "userProfilePk": 12345678,
    "hrvSummary": {
        "calendarDate": "2024-01-15",
        "weeklyAvg": 45,
        "lastNightAvg": 48,
        "lastNight5MinHigh": 52,
        "baseline": {
            "lowUpper": 35,
            "balancedLow": 40,
            "balancedUpper": 55,
            "markerValue": 0.5,
        },
        "status": "BALANCED",
        "feedbackPhrase": "HRV_BALANCED_2",
        "createTimeStamp": "2024-01-15T07:30:00.000",
    },
    "hrvReadings": [
        {"hrvValue": 45, "readingTimeGMT": "2024-01-15T00:15:00.0", "readingTimeLocal": "2024-01-15T00:15:00.0"},
        {"hrvValue": 48, "readingTimeGMT": "2024-01-15T00:20:00.0", "readingTimeLocal": "2024-01-15T00:20:00.0"},
        {"hrvValue": 52, "readingTimeGMT": "2024-01-15T00:25:00.0", "readingTimeLocal": "2024-01-15T00:25:00.0"},
    ],
    "sleepStartTimestampLocal": "2024-01-15T00:10:00.0",
    "sleepEndTimestampLocal": "2024-01-15T07:30:00.0",
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
    "workoutSegments": [
        {
            "segmentOrder": 1,
            "type": "WARMUP",
            "duration": 600,
            "targetType": "HEART_RATE_ZONE",
            "targetValue": 2
        },
        {
            "segmentOrder": 2,
            "type": "INTERVAL",
            "duration": 1200,
            "targetType": "PACE",
            "targetValue": 240  # seconds per km
        }
    ]
}

# Women's Health
MOCK_MENSTRUAL_DATA = {
    "calendarDate": "2024-01-15",
    "cycleDay": 15,
    "phase": "FOLLICULAR",
    "symptoms": []
}

# Weekly Health Metrics - APIs return lists of weekly aggregates
MOCK_WEEKLY_STEPS = [
    {
        "calendarDate": "2024-01-08",
        "values": {
            "totalSteps": 70000,
            "averageSteps": 10000,
            "totalDistance": 50000,
            "averageDistance": 7142,
            "wellnessDataDaysCount": 7,
        },
    },
    {
        "calendarDate": "2024-01-01",
        "values": {
            "totalSteps": 65000,
            "averageSteps": 9285,
            "totalDistance": 46000,
            "averageDistance": 6571,
            "wellnessDataDaysCount": 7,
        },
    },
]

MOCK_WEEKLY_STRESS = [
    {
        "calendarDate": "2024-01-08",
        "value": 35,
    },
    {
        "calendarDate": "2024-01-01",
        "value": 38,
    },
]

MOCK_WEEKLY_INTENSITY_MINUTES = [
    {
        "calendarDate": "2024-01-08",
        "weeklyGoal": 150,
        "moderateValue": 120,
        "vigorousValue": 45,
    },
    {
        "calendarDate": "2024-01-01",
        "weeklyGoal": 150,
        "moderateValue": 90,
        "vigorousValue": 30,
    },
]

MOCK_MORNING_TRAINING_READINESS = {
    "readinessScore": 75,
    "readinessLevel": "GOOD",
    "recoveryTime": 12,
    "hrvStatus": "BALANCED",
    "sleepQuality": "GOOD",
    "sleepScore": 82,
    "restingHeartRate": 55,
    "hrvBaseline": 65,
    "hrvLastNight": 68,
    "bodyBattery": 85,
    "stressLevel": 25,
}

# Activity Management
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

MOCK_ENDURANCE_SCORE = {
    "userProfilePK": 12345678,
    "startDate": "2024-01-08",
    "endDate": "2024-01-15",
    "avg": 5631,
    "max": 5740,
    "groupMap": {
        "2024-01-08": {
            "groupAverage": 5548,
            "groupMax": 5561,
            "enduranceContributorDTOList": [
                {"activityTypeId": 3, "group": None, "contribution": 8.89},
                {"activityTypeId": None, "group": 0, "contribution": 82.15},
                {"activityTypeId": None, "group": 1, "contribution": 4.10},
                {"activityTypeId": None, "group": 8, "contribution": 4.86},
            ],
        },
    },
    "enduranceScoreDTO": {
        "userProfilePK": 12345678,
        "deviceId": 1234567890,
        "calendarDate": "2024-01-15",
        "overallScore": 5712,
        "classification": 2,
        "feedbackPhrase": 38,
        "primaryTrainingDevice": True,
        "gaugeLowerLimit": 3570,
        "classificationLowerLimitIntermediate": 5100,
        "classificationLowerLimitTrained": 5800,
        "classificationLowerLimitWellTrained": 6500,
        "classificationLowerLimitExpert": 7200,
        "classificationLowerLimitSuperior": 7900,
        "classificationLowerLimitElite": 8600,
        "gaugeUpperLimit": 10320,
        "contributors": [
            {"activityTypeId": None, "group": 0, "contribution": 87.47},
            {"activityTypeId": 3, "group": None, "contribution": 5.49},
            {"activityTypeId": 163, "group": None, "contribution": 3.13},
            {"activityTypeId": None, "group": 8, "contribution": 3.91},
        ],
    },
}

# Training - Lactate Threshold
# Response format for latest=True
MOCK_LACTATE_THRESHOLD = {
    "speed_and_heart_rate": {
        "userProfilePK": 12345678,
        "calendarDate": "2024-01-15T10:30:00.000",
        "speed": 0.32222132,
        "heartRate": 169,
        "heartRateCycling": None,
    },
    "power": {
        "userProfilePk": 12345678,
        "calendarDate": "2024-01-15T11:00:00.000",
        "origin": "weight",
        "sport": "RUNNING",
        "functionalThresholdPower": 334,
        "weight": 73.0,
        "powerToWeight": 4.575,
        "isStale": False,
    },
}

# Response format for latest=False (date range)
MOCK_LACTATE_THRESHOLD_RANGE = {
    "speed": [
        {"from": "2024-01-08", "until": "2024-01-08", "series": "running", "value": 0.29444, "updatedDate": "2024-01-08"},
        {"from": "2024-01-12", "until": "2024-01-12", "series": "running", "value": 0.30555, "updatedDate": "2024-01-12"},
        {"from": "2024-01-15", "until": "2024-01-15", "series": "running", "value": 0.31666, "updatedDate": "2024-01-15"},
    ],
    "heartRate": [
        {"from": "2024-01-08", "until": "2024-01-08", "series": "running", "value": 165, "updatedDate": "2024-01-08"},
        {"from": "2024-01-12", "until": "2024-01-12", "series": "running", "value": 167, "updatedDate": "2024-01-12"},
        {"from": "2024-01-15", "until": "2024-01-15", "series": "running", "value": 169, "updatedDate": "2024-01-15"},
    ],
    "power": [
        {"from": "2024-01-15", "until": "2024-01-15", "series": "running", "value": 334.0, "updatedDate": "2024-01-15"},
    ],
}
