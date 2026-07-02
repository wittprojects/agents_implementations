---
name: weather
description: Report current weather conditions for a city.
when_to_use: The user asks about weather, temperature, or the forecast for a location.
---

# Weather skill

Follow these steps when the user asks about the weather:

1. Determine the city (and country/state if the name is ambiguous). If no location is
   given, ask the user which city they mean before continuing.
2. Call the `get_weather` tool with the resolved city name.
3. Summarize the result in one or two sentences: the temperature and conditions.
4. If the user asked about clothing or plans, add a brief, practical suggestion
   (e.g. "bring an umbrella") grounded in the returned conditions.

Keep the response concise and do not invent data beyond what the tool returns.
