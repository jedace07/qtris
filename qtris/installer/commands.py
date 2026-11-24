"""Commands for installer scripts"""

import glob
import json
import multiprocessing
import os
import shlex
import shutil
from gettext import gettext as _
from pathlib import Path
from typing import Any

from qtris.cache import is_file_in_custom_cache
from qtris.config import QtrisConfig
from qtris.exceptions import UnspecifiedVersionError
from qtris.installer.errors import ScriptingError
from qtris.installer.installer import QtrisInstaller
from qtris.monitored_command import MonitoredCommand
from qtris.runners import InvalidRunnerError, import_runner, import_task
from qtris.runners.wine import wine
from qtris.util import extract, legendary, nile, selective_merge, system
from qtris.util.gog import apply_gog_config, convert_gog_config_to_qtris, find_gog_config_dir, get_gog_config
from qtris.util.gogdl import clear_stale_manifest, run_gogdl
from qtris.util.jobs import schedule_repeating_at_idle
from qtris.util.log import logger
from qtris.util.store_cli import StoreClientError
from qtris.util.wine.launchers import LAUNCHER_SETUPS, repair_ea_launcher
from qtris.util.wine.wine import WINE_DEFAULT_ARCH, get_proton_version_or_default, get_wine_path_for_version


class CommandsMixin:
    """The directives for the `installer:` part of the install script."""

    # Set by ScriptInterpreter, which the directives run in
    installer: QtrisInstaller = NotImplemented
    target_path: str
    interpreter_ui_delegate: Any
    cancelled: bool
    launcher_setup: str

    def get_wine_path(self) -> str:
        """Return the program that runs Wine during the installation: the Proton build
        the script names, else the Wine runner's, else GE-Proton Latest."""
        runner = self.get_runner_class(self.installer.runner)()
        version = runner.get_installer_runner_version(self.installer, use_runner_config=False)
        if not version and self.installer.runner == "wine":
            try:
                version = wine.get_runner_version_and_config()[0]
            except UnspecifiedVersionError:
                pass
        return get_wine_path_for_version(get_proton_version_or_default(version))

    def get_runner_class(self, runner_name):
        """Runner the runner class from its name"""
        try:
            runner = import_runner(runner_name)
        except InvalidRunnerError as err:
            raise ScriptingError(_("Invalid runner provided %s") % runner_name) from err
        return runner

    @staticmethod
    def _check_required_params(params, command_data, command_name):
        """Verify presence of a list of parameters required by a command."""
        if isinstance(params, str):
            params = [params]
        for param in params:
            if isinstance(param, tuple):
                param_present = False
                for key in param:
                    if key in command_data:
                        param_present = True
                if not param_present:
                    raise ScriptingError(
                        _("One of {params} parameter is mandatory for the {cmd} command").format(
                            params=_(" or ").join(param), cmd=command_name
                        ),
                        faulty_data=command_data,
                    )
            else:
                if param not in command_data:
                    raise ScriptingError(
                        _("The {param} parameter is mandatory for the {cmd} command").format(
                            param=param, cmd=command_name
                        ),
                        faulty_data=command_data,
                    )

    def extract(self, data):
        """Extract a file, guessing the compression method."""
        self._check_required_params([("file", "src")], data, "extract")
        src_param = data.get("file") or data.get("src")
        filespec = self._get_file_path(src_param)

        if os.path.exists(filespec):
            filenames = [filespec]
        else:
            filenames = glob.glob(filespec)

        if not filenames:
            raise ScriptingError(_("%s does not exist") % filespec)
        if "dst" in data:
            dest_path = self._substitute(data["dst"])
        else:
            dest_path = self.target_path
        for filename in filenames:
            msg = _("Extracting %s") % os.path.basename(filename)
            logger.debug(msg)
            self.interpreter_ui_delegate.report_status(msg)
            merge_single = "nomerge" not in data
            extractor = data.get("format")
            extract_nested = bool(data.get("nested"))
            logger.debug("extracting file %s to %s", filename, dest_path)
            self._killable_process(
                extract.extract_archive, filename, dest_path, merge_single, extractor, extract_nested
            )
        logger.debug("Extract done")

    def mkdir(self, directory):
        """Create directory"""
        directory = self._substitute(directory)
        try:
            os.makedirs(directory)
        except OSError:
            logger.debug("Directory %s already exists", directory)
        else:
            logger.debug("Created directory %s", directory)

    def merge(self, params):
        """Merge the contents given by src to destination folder dst"""
        self._check_required_params(["src", "dst"], params, "merge")
        src, dst = self._get_move_paths(params)
        logger.debug("Merging %s into %s", src, dst)
        if not os.path.exists(src):
            if params.get("optional"):
                logger.info("Optional path %s not present", src)
                return
            raise ScriptingError(_("Source does not exist: %s") % src, faulty_data=params)
        os.makedirs(dst, exist_ok=True)
        if os.path.isfile(src):
            # If single file, copy it and change reference in game file so it
            # can be used as executable. Skip copying if the source is the same
            # as destination.
            if os.path.dirname(src) != dst:
                self._killable_process(shutil.copy, src, dst)
            if params["src"] in self.game_files.keys():
                self.game_files[params["src"]] = os.path.join(dst, os.path.basename(src))
            return
        self._killable_process(system.merge_folders, src, dst)
        # Optionally drop the source tree once its contents have been merged.
        # Useful when the source is throwaway scaffolding (e.g. the
        # installDirectory subfolder a gogdl depot download creates) that
        # would otherwise duplicate the merged data.
        if params.get("remove_source") and os.path.isdir(src):
            self._killable_process(shutil.rmtree, src)

    def copy(self, params):
        """Alias for merge"""
        self.merge(params)

    def move(self, params):
        """Move a file or directory into a destination folder."""
        self._check_required_params(["src", "dst"], params, "move")
        src, dst = self._get_move_paths(params)
        logger.debug("Moving %s to %s", src, dst)
        if not os.path.exists(src):
            if params.get("optional"):
                logger.info("Optional path %s not present", src)
                return
            raise ScriptingError(_("Invalid source for 'move' operation: %s") % src)

        if os.path.isfile(src):
            if os.path.dirname(src) == dst:
                logger.info("Source file is the same as destination, skipping")
                return

            if os.path.exists(os.path.join(dst, os.path.basename(src))):
                # May not be the best choice, but it's the safest.
                # Maybe should display confirmation dialog (Overwrite / Skip) ?
                logger.info("Destination file exists, skipping")
                return
        try:
            if is_file_in_custom_cache(src):
                action = shutil.copy
            else:
                action = shutil.move
            self._killable_process(action, src, dst)
        except shutil.Error as err:
            raise ScriptingError(_("Can't move {src} \nto destination {dst}").format(src=src, dst=dst)) from err

    def _get_move_paths(self, params):
        """Process raw 'src' and 'dst' data."""
        try:
            src_ref = params["src"]
        except KeyError as err:
            raise ScriptingError(_("Missing parameter src")) from err
        src = self.game_files.get(src_ref) or self._substitute(src_ref)
        if not src:
            raise ScriptingError(_("Wrong value for 'src' param"), faulty_data=src_ref)
        dst_ref = params["dst"]
        dst = self._substitute(dst_ref)
        if not dst:
            raise ScriptingError(_("Wrong value for 'dst' param"), faulty_data=dst_ref)
        return src.rstrip("/"), dst.rstrip("/")

    def substitute_vars(self, data):
        """Substitute variable names found in given file."""
        self._check_required_params("file", data, "substitute_vars")
        filename = self._substitute(data["file"])
        logger.debug("Substituting variables for file %s", filename)
        tmp_filename = filename + ".tmp"
        with open(filename, "r", encoding="utf-8") as source_file:
            with open(tmp_filename, "w", encoding="utf-8") as dest_file:
                line = "."
                while line:
                    line = source_file.readline()
                    line = self._substitute(line)
                    dest_file.write(line)
        os.rename(tmp_filename, filename)

    def _get_task_runner_and_name(self, task_name):
        if "." in task_name:
            # Run a task from a different runner
            # than the one for this installer
            runner_name, task_name = task_name.split(".")
        else:
            runner_name = self.installer.runner

        task_name = task_name.replace("-", "_")
        # Prevent private functions from being accessed as commands
        task_name = task_name.strip("_")
        return runner_name, task_name

    def task(self, data):
        """Directive triggering another function specific to a runner.

        The 'name' parameter is mandatory. If 'args' is provided it will be
        passed to the runner task.
        """
        self._check_required_params("name", data, "task")
        runner_name, task_name = self._get_task_runner_and_name(data.pop("name"))
        wine_prefix = ""

        if runner_name.startswith("wine"):
            wine_prefix = self.installer.wine_prefix
            data["wine_path"] = self.get_wine_path()
            if not wine_prefix:
                data["prefix"] = data.get("prefix") or self.installer.script.get("game", {}).get("prefix") or "$GAMEDIR"
            data["arch"] = data.get("arch") or self.installer.script.get("game", {}).get("arch") or WINE_DEFAULT_ARCH
            if task_name == "wineexec":
                data["env"] = self.script_env

        for key in data:
            value = data[key]
            if isinstance(value, dict):
                for inner_key in value:
                    value[inner_key] = self._substitute(value[inner_key])
            elif isinstance(value, list):
                for index, elem in enumerate(value):
                    value[index] = self._substitute(elem)
            else:
                value = self._substitute(data[key])
            data[key] = value

        if wine_prefix:
            data["prefix"] = wine_prefix
        launcher = self.installer.required_game
        if (
            task_name == "wineexec"
            and runner_name == "wine"
            and (self.launcher_setup or (launcher and getattr(self.installer.service, "client_installer", None)))
        ):
            config = QtrisConfig(runner_slug="wine", game_config_id=launcher["configpath"] if launcher else None)
            config.runner_config.update(self.installer.script.get("wine", {}))
            config.system_config["env"] = (config.system_config.get("env") or {}) | data["env"]
            data["runner"] = wine(config, prefix=data["prefix"])
        task = import_task(runner_name, task_name)
        command = task(**data)
        if isinstance(command, MonitoredCommand):
            if wine_prefix or self.launcher_setup:
                self.abort_current_task = command.stop
                if self.cancelled:
                    command.stop()
                    return "STOP"
            # Monitor thread and continue when task has executed
            self.interpreter_ui_delegate.attach_log(command)
            schedule_repeating_at_idle(self._monitor_task, command, interval_seconds=1.0)
            return "STOP"
        return None

    def finish_launcher_setup(self, service_id: str) -> None:
        setup = LAUNCHER_SETUPS[service_id]
        prefix = Path(self.target_path)
        executable = prefix / setup.executable
        repair_ea_launcher(prefix, executable)
        if not executable.is_file():
            raise ScriptingError(_("The launcher was not installed. Expected to find %s.") % executable)

    def _monitor_task(self, command):
        if self.cancelled:
            return False
        if not command.is_running:
            self.abort_current_task = None
            logger.debug("Return code: %s", command.return_code)
            if command.return_code != "0":
                raise ScriptingError(_("Command exited with code %s") % command.return_code)

            self._iter_commands()
            return False
        return True  # keep checking

    def write_json(self, params):
        """Write data into a json file."""
        self._check_required_params(["file", "data"], params, "write_json")

        # Get file
        filename = self._get_file_path(params["file"])

        # Create dir if necessary
        basedir = os.path.dirname(filename)
        os.makedirs(basedir, exist_ok=True)

        merge = params.get("merge", True)

        # create an empty file if it doesn't exist
        Path(filename).touch(exist_ok=True)

        with open(filename, "r+" if merge else "w", encoding="utf-8") as json_file:
            json_data = {}
            if merge:
                try:
                    json_data = json.load(json_file)
                except ValueError:
                    logger.error("Failed to parse JSON from file %s", filename)

            json_data = selective_merge(json_data, params.get("data", {}))
            json_file.seek(0)
            json_file.truncate()
            json_file.write(json.dumps(json_data, indent=2))

    def _get_file_path(self, fileid):
        file_path = self.game_files.get(fileid)
        if not file_path:
            file_path = self._substitute(fileid)
        return file_path

    def _killable_process(self, func, *args, **kwargs):
        """Run function `func` in a separate, killable process."""
        with multiprocessing.Pool(1) as process:
            result_obj = process.apply_async(func, args, kwargs)
            self.abort_current_task = process.terminate
            result = result_obj.get()  # Wait process end & re-raise exceptions
            self.abort_current_task = None
            logger.debug("Process %s returned: %s", func, result)
            return result

    def _extract_innosetup(self, file_id):
        self.extract({"src": file_id, "dst": "$GAMEDIR", "format": "gog"})
        app_path = os.path.join(self.target_path, "app")
        if system.path_exists(app_path):
            for app_content in os.listdir(app_path):
                source_path = os.path.join(app_path, app_content)
                if os.path.exists(os.path.join(self.target_path, app_content)):
                    self.merge({"src": source_path, "dst": self.target_path})
                else:
                    self.move({"src": source_path, "dst": self.target_path})
        support_path = os.path.join(self.target_path, "__support/app")
        if system.path_exists(support_path):
            self.merge({"src": support_path, "dst": self.target_path})

    def _use_bundled_engine(self, launch: dict) -> None:
        """Set up a DOS or ScummVM game to run with the Windows DOSBox or ScummVM it bundles."""
        self.installer.script["game"] = {"prefix": "$GAMEDIR/pfx", **launch}

    def _find_bundled_engine(self, filename: str) -> str:
        if found := sorted(Path(self.target_path).rglob(filename, case_sensitive=False)):
            return str(found[0])
        raise ScriptingError(_("The installer is missing %s.") % filename)

    def autosetup_gog_game(self, file_id, silent=False):
        """Automatically guess the best way to install a GOG game by inspecting its contents.
        DOS and ScummVM games are extracted, as Proton runs the engine they bundle.
        Linux setup files don't use innosetup, they can be unzipped instead.
        """
        file_path = self.game_files[file_id]
        file_list = extract.get_innoextract_list(file_path)
        dosbox_found = False
        scummvm_found = False
        windows_override_found = False  # DOS games that also have a Windows executable
        for filename in file_list:
            if "dosbox.exe" in filename.casefold():
                dosbox_found = True
            if "scummvm.exe" in filename.casefold():
                scummvm_found = True
            if "_some_windows.exe" in filename.casefold():
                # There's not a good way to handle exceptions without extracting the .info file
                # before extracting the game. Added for Quake but GlQuake.exe doesn't run on modern wine
                windows_override_found = True
        if scummvm_found or (dosbox_found and not windows_override_found):
            self._extract_innosetup(file_id)
            config_dir = find_gog_config_dir(self.target_path)
            gog_config = get_gog_config(config_dir) if config_dir else None
            launch = convert_gog_config_to_qtris(gog_config, config_dir) if config_dir else {}
            if not launch.get("exe"):
                raise ScriptingError(_("The installer doesn't say how to start this game."))
            self._use_bundled_engine(launch)
        else:
            args = "/SP- /NOCANCEL"
            if silent:
                args += " /SUPPRESSMSGBOXES /VERYSILENT /NOGUI"
            self.installer.post_install_hooks.append(apply_gog_config)
            return self.task({"name": "wineexec", "prefix": "$GAMEDIR", "executable": file_id, "args": args})

    def _get_dosbox_arguments(self, zoom_bat_path):
        """Return the arguments for the DOSBox executable from a ZOOM Plaform installer"""
        with open(zoom_bat_path, encoding="utf-8") as zoom_bat_file:
            zoom_bat = zoom_bat_file.read()

        # Find the line that starts with ".\DOSBOX\dosbox.exe" and extract the arguments
        for line in zoom_bat.splitlines():
            if line.startswith(".\\DOSBOX\\dosbox.exe"):
                # Batch files don't escape with backslashes, which splitting arguments would drop
                return line.partition(" ")[2].strip().replace("\\", "/")
        return ""

    def _get_dosbox_config_arguments(self) -> str:
        # The file that starts the game goes last, as its [autoexec] section mounts the drives
        configs = sorted(
            (name for name in os.listdir(self.target_path) if name.endswith(".conf")),
            key=lambda name: (name.endswith("_single.conf"), name == "dosbox.conf", name),
        )
        return " ".join(f'-conf "{name}"' for name in configs)

    def autosetup_zoom_platform(self, file_id: str, silent: bool = False) -> Any:
        """Set up a native ZOOM build or run its Windows installer."""
        if self.installer.runner == "linux":
            self.extract({"file": file_id, "dst": "$GAMEDIR"})
            target = Path(self.target_path)
            launchers = sorted(target.rglob("start.sh"), key=lambda path: (len(path.parts), path))
            if launchers:
                launcher = launchers[0]
                system.make_executable(str(launcher))
                self.installer.script["game"].update(exe=str(launcher), working_dir=str(launcher.parent))
            return None

        file_path = self.game_files[file_id]
        file_list = extract.get_innoextract_list(file_path)
        dosbox_found = False
        scummvm_found = False
        for filename in file_list:
            if "dosbox.exe" in filename.casefold():
                dosbox_found = True
            if "scummvm.exe" in filename.casefold():
                scummvm_found = True
        if dosbox_found:
            self._extract_innosetup(file_id)
            arguments = ""
            for filename in os.listdir(self.target_path):
                if filename.startswith("Launch") and filename.endswith(".bat"):
                    arguments = self._get_dosbox_arguments(os.path.join(self.target_path, filename))
            self._use_bundled_engine(
                {
                    "exe": self._find_bundled_engine("dosbox.exe"),
                    "args": arguments or self._get_dosbox_config_arguments(),
                    "working_dir": self.target_path,
                }
            )
        elif scummvm_found:
            self._extract_innosetup(file_id)
            self._use_bundled_engine(
                {
                    "exe": self._find_bundled_engine("scummvm.exe"),
                    "args": "--path=Data --auto-detect",
                    "working_dir": self.target_path,
                }
            )
        else:
            install_dir = "Z:" + self.target_path.replace("/", "\\")
            args = f"/SP- /NOCANCEL /NORESTART /NOICONS {shlex.quote(f'/DIR={install_dir}')}"
            if silent:
                args += " /SUPPRESSMSGBOXES /VERYSILENT /NOGUI"
            return self.task({"name": "wineexec", "prefix": "$GAMEDIR", "executable": file_id, "args": args})
        return None

    def legendary_setup(self, data):
        """Download, update or repair an Epic Games Store game with legendary.

        Params:
            app_name: the game's app name (defaults to the installer's app ID)
            command: "download" | "update" | "repair" (default: "download")
        """
        app_name = str(data.get("app_name") or self.installer.service_appid or "")
        command = data.get("command", "download")
        if not app_name:
            raise ScriptingError(_("legendary_setup requires an app_name."), faulty_data=data)
        installed = self._run_store_client(
            _("Epic Games Store"), lambda progress: legendary.install(app_name, self.target_path, command, progress)
        )
        if command == "download" and installed.get("executable"):
            exe = os.path.join(installed["install_path"], installed["executable"])
            self.installer.script.setdefault("game", {}).setdefault("exe", exe)

    def nile_setup(self, data):
        """Download, update or repair an Amazon Games game with Nile.

        Params:
            game_id: the game's ID (defaults to the installer's app ID)
            command: "download" | "update" | "repair" (default: "download")
        """
        game_id = str(data.get("game_id") or self.installer.service_appid or "")
        command = data.get("command", "download")
        if not game_id:
            raise ScriptingError(_("nile_setup requires a game_id."), faulty_data=data)
        self._run_store_client(_("Amazon"), lambda progress: nile.install(game_id, self.target_path, command, progress))
        if command == "download":
            # Nile reads the game's executable from its fuel.json
            launch = nile.get_launch_parameters(game_id)
            exe = (launch.get("command") or {}).get("instruction")
            if exe:
                self.installer.script.setdefault("game", {}).setdefault("exe", exe)

    def _run_store_client(self, store, install):
        """Run a store client's download, reporting its progress; return what the
        client knows of the installed game."""
        os.makedirs(self.target_path, exist_ok=True)
        self.interpreter_ui_delegate.report_status(_("Downloading from %s...") % store)
        self.interpreter_ui_delegate.report_progress(-1)

        def progress_callback(progress):
            parts = []
            if progress.speed_mib > 0:
                parts.append(_("{speed:.1f} MiB/s").format(speed=progress.speed_mib))
            if progress.eta and progress.eta != "00:00:00":
                parts.append(_("ETA: {eta}").format(eta=progress.eta))
            self.interpreter_ui_delegate.report_progress(progress.fraction, " — ".join(parts))

        try:
            installed = install(progress_callback)
        except StoreClientError as err:
            raise ScriptingError(_("%(store)s download failed: %(error)s") % {"store": store, "error": err}) from err
        self.interpreter_ui_delegate.report_progress(None)
        return installed

    def gogdl_setup(self, data):
        """Download and set up a GOG game via depot/CDN using heroic-gogdl.

        Params:
            game_id: GOG product ID (defaults to the installer's resolved
                gogid, so scripts only need to set it once; an explicit value
                still wins, e.g. for downloading a DLC)
            platform: "windows" or "linux" (default: based on runner)
            lang: language code (default: system locale)
            dlcs: "all", "none", or comma-separated DLC IDs (optional)
            preserve_manifest: True to keep an existing gogdl manifest (use when
                adding DLCs to an installed base game, so gogdl diffs old→new
                and only downloads the DLC files instead of treating the base
                as missing)
            command: "download" | "update" | "repair" (default: "download")
        """
        game_id = data.get("game_id") or self.installer.service_appid
        if not game_id:
            raise ScriptingError(
                _("gogdl_setup requires a game_id, or a gogid set on the installer."),
                faulty_data=data,
            )

        game_id = str(game_id)
        command = data.get("command", "download")
        platform = data.get("platform")
        lang = data.get("lang")
        dlcs_param = data.get("dlcs")
        preserve_manifest = bool(data.get("preserve_manifest"))

        if not platform:
            platform = "linux" if self.installer.runner == "linux" else "windows"

        # Determine install path
        # gogdl appends the game's installDirectory (from the depot manifest)
        # to the path on downloads, so we point it at the parent only; but
        # update and repair write into the path as given, so they need the
        # game directory itself.
        install_path = self.target_path
        if command in ("update", "repair"):
            install_path = self._find_gogdl_game_path(install_path)

        os.makedirs(install_path, exist_ok=True)

        # Handle DLC parameter
        if dlcs_param == "all":
            dlcs = True
        elif dlcs_param == "none":
            dlcs = False
        elif dlcs_param:
            dlcs = str(dlcs_param)
        else:
            dlcs = None

        # Clear stale manifest for fresh downloads so gogdl doesn't
        # think the game is already installed from a previous attempt.
        if command == "download" and not preserve_manifest:
            clear_stale_manifest(game_id, install_path, runner="linux" if platform == "linux" else "wine")

        if command == "repair":
            msg = _("Verifying and repairing via GOG depot...")
        else:
            msg = _("Downloading via GOG depot...")
        logger.info(msg)
        self.interpreter_ui_delegate.report_status(msg)
        # gogdl reports nothing until it starts downloading, and a repair may
        # spend a long time just verifying files first.
        self.interpreter_ui_delegate.report_progress(-1)

        def progress_callback(progress):
            fraction = progress.percent / 100.0
            parts = []
            if progress.speed_mib > 0:
                parts.append(_("{speed:.1f} MiB/s").format(speed=progress.speed_mib))
            if progress.eta and progress.eta != "00:00:00":
                parts.append(_("ETA: {eta}").format(eta=progress.eta))
            text = " — ".join(parts) if parts else ""
            self.interpreter_ui_delegate.report_progress(fraction, text)

        try:
            outcome = run_gogdl(
                command=command,
                game_id=game_id,
                path=install_path,
                platform=platform,
                lang=lang,
                dlcs=dlcs,
                progress_callback=progress_callback,
            )
        except FileNotFoundError as err:
            raise ScriptingError(
                _("gogdl hasn't been downloaded yet. Check for updates in Preferences → Updates, then try again.")
            ) from err
        except Exception as err:
            raise ScriptingError(_("GOG depot download failed: %s") % err) from err

        self.interpreter_ui_delegate.report_progress(None)

        if command == "repair":
            if outcome.nothing_to_do:
                self.install_complete_text = _("All game files are intact; nothing needed repairing.")
            elif outcome.broken_files is not None:
                self.install_complete_text = _("Damaged or missing files repaired: %d") % outcome.broken_files
            else:
                self.install_complete_text = _("Damaged or missing files were repaired.")
        elif command == "update" and outcome.nothing_to_do:
            self.install_complete_text = _("The game is already up to date.")

        # Post-install: find where gogdl actually put the files and apply config.
        # For a fresh "download", gogdl appends installDirectory to the path, so we
        # need to discover the actual game directory. For update and repair,
        # install_path is already the game directory.
        if command == "download" and not preserve_manifest:
            game_path = self._find_gogdl_game_path(install_path)
        else:
            game_path = install_path

        self._apply_gogdl_post_install(game_path, platform, game_id)
        logger.info("GOG depot download completed for %s", game_id)

    def _find_gogdl_game_path(self, parent_path):
        """Find the actual game directory under parent_path after a gogdl download.
        gogdl creates a subdirectory named after the game's installDirectory."""
        if not os.path.isdir(parent_path):
            return parent_path
        entries = [e for e in os.listdir(parent_path) if os.path.isdir(os.path.join(parent_path, e))]
        if len(entries) == 1:
            return os.path.join(parent_path, entries[0])
        # Multiple dirs or none — look for goggame-*.info to find the right one
        for entry in entries:
            candidate = os.path.join(parent_path, entry)
            if any(f.startswith("goggame") and f.endswith(".info") for f in os.listdir(candidate)):
                return candidate
        return parent_path

    def _apply_gogdl_post_install(self, install_path: str, platform: str, product_id: str) -> None:
        """Detect game configuration from goggame-*.info after a depot download."""
        config_path = find_gog_config_dir(install_path, product_id)
        if not config_path:
            return

        qtris_config = convert_gog_config_to_qtris(get_gog_config(config_path, product_id), config_path)
        if qtris_config and any(
            engine in config["exe"].casefold()
            for config in (qtris_config, *qtris_config["launch_configs"])
            for engine in ("dosbox", "scummvm")
        ):
            self._place_gog_support_files(config_path)

        if qtris_config and "game" in self.installer.script:
            # For Linux GOG games, prefer start.sh over the binary from goggame info.
            # GOG's start.sh handles working directory setup and permissions.
            if platform == "linux":
                start_sh = os.path.join(install_path, "start.sh")
                if os.path.isfile(start_sh):
                    qtris_config["exe"] = start_sh
                    qtris_config["working_dir"] = install_path
                    qtris_config.pop("args", None)
                    system.make_executable(start_sh)
            # Don't clobber config the script set explicitly: a community
            # installer may ship its own engine (e.g. a native source port
            # run over GOG game data), in which case its exe must win over the
            # Windows exe detected from goggame-*.info. Auto-generated GOG
            # installers leave these unset, so detection still fills them in.
            game_config = self.installer.script["game"]
            if not game_config.get("exe"):
                for key, value in qtris_config.items():
                    game_config.setdefault(key, value)

    def _place_gog_support_files(self, game_path: str) -> None:
        """Copy in the settings that bundled DOSBox and ScummVM builds are started with.

        gogdl leaves them in gog-support, and GOG's setup would copy them into the game's
        folder. Files already there are the user's, so they're kept.
        """
        for app in sorted(Path(game_path).glob("gog-support/*/app")):
            for source in app.rglob("*"):
                target = Path(system.fix_path_case(os.path.join(game_path, source.relative_to(app))))
                if source.is_file() and not target.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy(source, target)

    def install_or_extract(self, file_id):
        """Runs if file is executable or extracts if file is archive"""
        file_path = self._get_file_path(file_id)
        runner = self.installer.runner
        if runner != "wine":
            raise ScriptingError(_("install_or_extract only works with wine!"))
        if file_path.endswith(".exe"):
            params = {"name": "wineexec", "executable": file_id}
            return self.task(params)

        slug = self.installer.game_slug
        params = {"file": file_id, "dst": f"$GAMEDIR/drive_c/{slug}"}
        return self.extract(params)

    @staticmethod
    def _is_installer_exe(path):
        """Check if an .exe file is a known installer (NSIS or Inno Setup)."""
        try:
            with open(path, "rb") as f:
                header = f.read(512 * 1024)
            return b"NullsoftInst" in header or b"Inno Setup" in header
        except OSError:
            return False

    def extract_or_run(self, data):
        """Extract an archive. If extraction yields an installer exe (NSIS/Inno),
        run it with Wine instead of placing the files."""
        self._check_required_params("file", data, "extract_or_run")
        file_id = data["file"]
        dst = self._substitute(data.get("dst", "$CACHE"))
        file_path = self._get_file_path(file_id)

        # Extract the archive (e.g. tar.gz wrapper)
        self._killable_process(extract.extract_archive, file_path, dst, True, extract_nested=True)

        # Check if any extracted .exe is an installer
        for entry in os.listdir(dst):
            entry_path = os.path.join(dst, entry)
            if entry.lower().endswith(".exe") and os.path.isfile(entry_path) and self._is_installer_exe(entry_path):
                logger.info("Detected installer exe: %s, running with Wine", entry)
                params = {"name": "wineexec", "executable": entry_path}
                return self.task(params)

        # No installer found, merge the extracted files to the destination
        merge_dst = self._substitute(data.get("merge_dst", data.get("dst", "$GAMEDIR")))
        if merge_dst != dst:
            self.merge({"src": dst, "dst": merge_dst})
