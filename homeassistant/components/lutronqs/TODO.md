2026-04-10 13:01:26.724 WARNING (MainThread) [homeassistant.helpers.frame] Detected that custom integration 'lutronqs' calls `device_registry.async_get_or_create` referencing a non existing `via_device` ('lutronqs', '00F63FBE'), with device info: {'hw_version': '2.1', 'identifiers': {('lutronqs', '00BD9515')}, 'manufacturer': 'Lutron', 'model': 'GRAFIK_EYE(2) - QSG-ECO(2)', 'name': '00BD9515', 'sw_version': 'Boot: 4.1 Code: 8.27', 'via_device': ('lutronqs', '00F63FBE')} at custom_components/lutronqs/__init__.py, line 167: device_entry = device_registry.async_get_or_create(. This will stop working in Home Assistant 2025.12.0, please report it to the author of the 'lutronqs' custom integration

(maybe we need to order them more cleverly?)




2026-04-10 13:39:44.376 ERROR (MainThread) [custom_components.lutronqs] Error in unsolicited message monitoring
Traceback (most recent call last):
  File "/config/custom_components/lutronqs/__init__.py", line 383, in monitor_unsolicited_messages
    message = await conn.read_unsolicited()
              ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.14/site-packages/lutron_integration/connection.py", line 381, in read_unsolicited
    await self.__wait_for_data(lambda: len(self.__unsolicited_queue) >= 1)
  File "/usr/local/lib/python3.14/site-packages/lutron_integration/connection.py", line 301, in __wait_for_data
    await self.__read_and_dispatch()
  File "/usr/local/lib/python3.14/site-packages/lutron_integration/connection.py", line 251, in __read_and_dispatch
    data = await self.__read_one_message()
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.14/site-packages/lutron_integration/connection.py", line 199, in __read_one_message
    raise DisconnectedError()
lutron_integration.connection.DisconnectedError: Disconnected



Traceback (most recent call last):
  File "/config/custom_components/lutronqs/__init__.py", line 383, in monitor_unsolicited_messages
    message = await conn.read_unsolicited()
              ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.14/site-packages/lutron_integration/connection.py", line 381, in read_unsolicited
    await self.__wait_for_data(lambda: len(self.__unsolicited_queue) >= 1)
  File "/usr/local/lib/python3.14/site-packages/lutron_integration/connection.py", line 301, in __wait_for_data
    await self.__read_and_dispatch()
  File "/usr/local/lib/python3.14/site-packages/lutron_integration/connection.py", line 255, in __read_and_dispatch
    if not self.__is_message_a_reply(data):
           ~~~~~~~~~~~~~~~~~~~~~~~~~^^^^^^
  File "/usr/local/lib/python3.14/site-packages/lutron_integration/connection.py", line 238, in __is_message_a_reply
    assert b"\r\n" not in message[:-2], (
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^
AssertionError: Unsolicited message b'login: connection established\r\n~MONITORING,1,1\r\n' has too many lines



 One adjacent HA issue I noticed but did not change: core/homeassistant/components/
  lutronqs/__init__.py:373 captures entry.runtime_data.connection once at startup.
  After reconnect, that task will keep reading the old connection object instead of
  the replacement. That is separate from your traceback, but it will matter once
  reconnects are exercised.