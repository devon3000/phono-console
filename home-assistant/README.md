# Home Assistant setup

1. Copy `phono_console.yaml` into Home Assistant's `packages` directory.
2. Replace `PHONO_CONSOLE_IP` with the Raspberry Pi's reserved LAN address.
3. Ensure packages are enabled in `configuration.yaml`, check the configuration,
   and restart Home Assistant:

   ```yaml
   homeassistant:
     packages: !include_dir_named packages
   ```

The package exposes the automatically selected source, effective distribution
path, input levels, phono/Bluetooth activity, and Sendspin status. There is no
manual source control. Phono defaults to minimum-latency console playback and
has a sticky Console/Downstairs output choice in the web dashboard; Bluetooth
normally distributes through Music Assistant.
