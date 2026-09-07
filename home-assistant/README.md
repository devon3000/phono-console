# Home Assistant setup

1. Copy `phono_console.yaml` into Home Assistant's `packages` directory.
2. Replace `PHONO_CONSOLE_IP` with the Raspberry Pi's reserved LAN address.
3. Read the generated token on the Pi:

   ```bash
   sudo sed -n 's/^PHONO_CONSOLE_API_TOKEN=//p' /etc/phono-console/environment
   ```

4. Add it to Home Assistant's `secrets.yaml`:

   ```yaml
   phono_console_authorization: "Bearer YOUR_GENERATED_TOKEN"
   ```

5. Ensure packages are enabled in `configuration.yaml`, check the configuration,
   and restart Home Assistant:

   ```yaml
   homeassistant:
     packages: !include_dir_named packages
   ```

The package exposes route, input level, phono activity, Sendspin connection and
streaming status, plus explicit actions to start or stop whole-house vinyl.
