2026-04-10 13:01:26.724 WARNING (MainThread) [homeassistant.helpers.frame] Detected that custom integration 'lutronqs' calls `device_registry.async_get_or_create` referencing a non existing `via_device` ('lutronqs', '00F63FBE'), with device info: {'hw_version': '2.1', 'identifiers': {('lutronqs', '00BD9515')}, 'manufacturer': 'Lutron', 'model': 'GRAFIK_EYE(2) - QSG-ECO(2)', 'name': '00BD9515', 'sw_version': 'Boot: 4.1 Code: 8.27', 'via_device': ('lutronqs', '00F63FBE')} at custom_components/lutronqs/__init__.py, line 167: device_entry = device_registry.async_get_or_create(. This will stop working in Home Assistant 2025.12.0, please report it to the author of the 'lutronqs' custom integration

(maybe we need to order them more cleverly?)

