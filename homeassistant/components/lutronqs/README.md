[This will get moved to the home-assistant.io repo]

---
title: Lutron QS Standalone
description: Control and monitoring of Lutron QS Standalone systems via a QSE-CI-NWK-E gateway.
ha_category:
  - Sensor
ha_release: '2026.04'
ha_iot_class: Local Polling
ha_codeowners:
  - '@amluto'
ha_domain: lutronqs
ha_platforms:
  - cover
  - light
  - remote
ha_config_flow: true
ha_integration_type: hub
---

Integrates Lutron QS Stanalone systems into Home Assistant.

Lutron provides an extensive portfolio of commercial and high-end residential devices
that connect to each other using a wired "QS" bus. Some users use Homeworks QS/QSX or
even higher-end central control mechanisms, but many of Lutron's QS devices can also
operate in "QS Standalone" mode without any central control at all.  Lutron's QSE-CI-NWK-E
interface can be used to bridge between a QS Standalone bus and an IPv4 network.

This requires a Lutron QSE-CI-NWK-E interface.

{% include integrations/config_flow.md %}

Lutron QSE-CI-NWK-E interfaces are not automatically discoverable, and they don't
even support DHCP.  The only way to use them is with static IPv4 addressing or using
a direct serial connection.  This integration only supports IPv4, although serial could
be added.

They are intended for professional installation, so installation is straightforward
but intended for power users.  After a factory reset, they will claim the IPv4
address 192.168.250.1/24, and you will need to configure a computer that has a
telnet client onto that network.  You can telnet in and log in as the user 'nwk' or 'nwk2'.
You may or may not see a prompt:

`QSE>`

(You can turn the prompt on and off.)  At the prompt, if any, you can query the current
IP address with the command `?ETHERNET,0` and you can and should change it using
`#ETHERNET,0,[new ipv4 address]` and `#ETHERNET,2,[subnet mask]'.  Then you can type
`#RESET,0` to reboot the device so it claims its new IP address.  You can also use `#ETHERNET,1,[gateway IP]`, and the default of `255.255.255.255` means "no gateway".  There is no need
to configure a gateway if you only intend to use the device over the local subnet.

Lutron QS Standalone supports a concept of an "integration ID", and you can read about
it in the Lutron Integration Protocol documentation.  Briefly, both "devices" (physical
Lutron devices on the network) and "outputs" (zones on a device") can be assigned a textual
identifier.  This integration will use them to populate the initial names of devices but
will otherwise do its best to ignore them.  Annoyingly, the acutal integration protocol
works differently for outputs with an id and outputs without an id, and the protocol
for outputs without an id is somewhat nicer.  Nonetheless, the integration attempts
to support both variants equally.

## Supported devices

- Lutron QSE-CI-NWK-E
- Lutron Grafik Eye QS (should support all variants)
  - The integration should support all lighting zone types connected to a Grafik Eye QS, in
    the same way that the zone buttons on the Grafik Eye QS itself supports them.  There is no
    ability to control zone details that the Grafik Eye does not expose.  For example, DMX
    lights will only be supported to the extent that they are mapped to zones.
- Lutron QS-connected keypads (should support all variants, even those sold for HomeWorks)
- Lutron Sivoia QS shades (if the wireless dongle is connected to the shades, then they may not connect to the wired bus)
  - There are a lot of variants of Sivoia QS shares.  It's possible that not all of them are mapped fully correctly to Home Assistant cover entities.
- Lutron Energi Savr Node (probably all variants) to the extent that they are mapped to
  a Grafik Eye QS.  More direct support should be possible but is not yet implemented.

## Updating Data

The integration polls the QSE-CI-NWK-E for changes to the set of available devices every
30 seconds.  Actual state updates are pushed from the device in real time without polling.
Many Lutron devices have transitions that take finite time (lights work this way, as do shades): in general, state updates will be reported when a transition starts and again when
the transition completes.

## Supported functionality

### Lights

Grafik Eye QS zones are exposed as light entities.  The standard `light` control actions
are supported.  If no transition time is specified, then a command will be sent to
the device without a transition time, and the device will use its default, which seems
to be 1 second on and 3 seconds off.  There is no obvious way to reconfigure this.
If a transition time is set on the action call, then it will be respected.

### Covers

Shares are exposed as cover entities.

### Remote

Grafik Eye QS scene controls are exposed as a single remote entity for the "scene controller"
for each per Grafik Eye QS.

Selecting an activity from Home Assistant will activate the corresponding scene.  Instructing
the remote to turn off will select the off scene.  Scene changes initiated from keypads
or from the Grafik Eye itself will be reflected in the scene controller entity.

### Custom Actions

There are no custom actions.

### Devices

Each physical Lutron device on the network, including the QSE-CI-NWK-E interface
itself, is exposed as a Home Assistant device.  Some of them may have no entities.

### Sensors

Lutron sells several types of sensors, but none of them are supported yet.

## Removing the integration

{% include integrations/remove_device_service.md %}