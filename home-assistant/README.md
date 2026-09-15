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

The package exposes the automatically selected source, effective distribution
path, input levels, phono/Bluetooth activity, and Sendspin status. There is no
manual source control: phono and Bluetooth normally play on `Downstairs`
through Music Assistant and use direct local output only as an outage fallback.
