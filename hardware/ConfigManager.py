import uos, gc
import ujson as json


class Config_Manager:
    def __init__(self, filename, default_config_file=None, default_config={}):
        self.config_file = filename

        # Load defaults from file when available; otherwise use given dict
        if default_config_file:
            try:
                with open(default_config_file, 'r') as f:
                    self.default_config = json.load(f)
            except Exception:
                self.default_config = default_config
        else:
            self.default_config = default_config

        # Create config file if missing
        if self.config_file not in uos.listdir():
            self.save_config(self.default_config)

    # --- Helpers: always pretty-print with indent=4 ---
    def _dump_json(self, data, file_obj):
        try:
            # CPython / some ports of ujson support indent
            json.dump(data, file_obj, indent=4)
        except TypeError:
            # MicroPython ujson: no indent -> custom pretty printer
            file_obj.write(self._pretty_json(data))

    def _pretty_json(self, obj, level=0, indent=4):
        sp = ' ' * (level * indent)
        sp_in = ' ' * ((level + 1) * indent)

        if isinstance(obj, dict):
            items = []
            for k in obj:
                v = obj[k]
                key = json.dumps(k)
                items.append(f"{sp_in}{key}: {self._pretty_json(v, level + 1, indent)}")
            inner = ',\n'.join(items)
            return "{\n" + inner + ("\n" + sp if items else "") + "}"
        elif isinstance(obj, (list, tuple)):
            items = [f"{sp_in}{self._pretty_json(v, level + 1, indent)}" for v in obj]
            inner = ',\n'.join(items)
            return "[\n" + inner + ("\n" + sp if items else "") + "]"
        elif isinstance(obj, (str, int, float)) or obj is None or isinstance(obj, bool):
            return json.dumps(obj)
        else:
            # Fallback to string representation
            return json.dumps(str(obj))

    def load_config(self):
        try:
            with open(self.config_file, 'r') as f:
                config = json.load(f)
        except Exception as e:
            print(f"[WARNING]: Load config failed: {e}; using defaults")
            config = self.default_config.copy()
        gc.collect()
        return config

    def save_config(self, config):
        try:
            current_config = self.load_config()
            current_config.update(config)
            with open(self.config_file, 'w') as f:
                self._dump_json(current_config, f)
            print("[SUCCESS]: Saved config")
            gc.collect()
            return current_config
        except Exception as e:
            print(f"[ERROR]: Save config failed: {e}")
            gc.collect()
            return self.default_config.copy()

    def get_config(self, key, default=None):
        config = self.load_config()
        return config.get(key, default)

    def set_config(self, key, value):
        return self.save_config({key: value})

    def reset_config(self, keys=None):
        try:
            if keys is None:
                with open(self.config_file, 'w') as f:
                    self._dump_json(self.default_config.copy(), f)
                print("[SUCCESS]: Reset all config")
                gc.collect()
                return

            config = self.load_config()
            keys_to_reset = keys if isinstance(keys, list) else [keys]

            for k in keys_to_reset:
                if k in self.default_config:
                    config[k] = self.default_config[k]
                    print(f"[SUCCESS]: Reset '{k}'")
                elif k in config:
                    del config[k]
                    print(f"[WARNING]: Key '{k}' not in default_config; deleted")
                else:
                    print(f"[WARNING]: Key '{k}' not found in config")

            with open(self.config_file, 'w') as f:
                self._dump_json(config, f)
            gc.collect()
        except Exception as e:
            print(f"[ERROR]: Reset config failed: {e}")
            gc.collect()
