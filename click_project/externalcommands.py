#!/usr/bin/env python
# -*- coding:utf-8 -*-

from __future__ import print_function, absolute_import

import os
import subprocess
import re
import importlib

import click

from click_project.commandresolver import CommandResolver
from click_project.config import config, get_settings_for_path
from click_project.lib import which, updated_env, quote
from click_project.log import get_logger

LOGGER = get_logger(__name__)


def external_cmds_paths():
    paths = []
    if config.project:
        paths.extend(config.project_bin_dirs)
    paths.append(os.path.join(config.app_dir, "scripts"))
    paths.extend(os.environ["PATH"].split(os.pathsep))
    return paths


class ExternalCommandResolver(CommandResolver):

    def _list_command_paths(self, parent=None):
        prefix = config.app_name + "-"
        if not hasattr(self, "_external_cmds"):
            self._external_cmds = []
            paths = external_cmds_paths()
            for path in paths:
                if os.path.isdir(path):
                    for file in os.listdir(path):
                        if file.startswith(prefix):
                            for suffix in ".sh", ".py":
                                if file.endswith(suffix):
                                    self._external_cmds.append(file[len(prefix):-3] + suffix.replace(".", "@"))
                                    break
                            else:
                                self._external_cmds.append(file[len(prefix):].replace(".", "@"))
        return self._external_cmds

    def _get_command(self, path, parent=None):
        prefix = config.app_name + "-"
        name = path.replace("@", ".")
        cmdhelp = "external command"
        command_name = prefix + name
        paths = external_cmds_paths()
        command_path = which(command_name, os.pathsep.join(paths))
        options = []
        arguments = []
        flags = []
        remaining_args = "Remaining arguments"
        try:
            process = subprocess.Popen([command_path, "--help"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            out, err = process.communicate()
            if process.returncode == 0:
                out = out.decode("utf-8")
                cmdhelp_lines = out.splitlines() + ['']
                try:
                    index_desc = cmdhelp_lines.index('') + 1
                except ValueError:
                    index_desc = 0
                try:
                    metadata_desc = cmdhelp_lines.index('--')
                    remaining_args = False
                except ValueError:
                    metadata_desc = -1
                cmdhelp = "\n".join(cmdhelp_lines[index_desc:metadata_desc])
                cmdhelp = cmdhelp.strip()
                metadata_out = out[metadata_desc:]
                for l in metadata_out.splitlines():
                    if l.startswith("O:"):
                        m = re.match("^O:(?P<name>[^:]+):(?P<type>[^:]+):(?P<help>[^:]+)$", l)
                        if m is None:
                            raise click.UsageError(
                                "Expected format in {} is O:name:type:help,"
                                " got {}".format(path, l))
                        options.append(
                            m.groupdict()
                        )
                    if l.startswith("F:"):
                        m = re.match("^F:(?P<name>[^:]+):(?P<help>[^:]+)(:(?P<default>[^:]+))?$", l)
                        if m is None:
                            raise click.UsageError(
                                "Expected format in {} is F:name:help,"
                                " got {}".format(path, l))
                        flags.append(
                            m.groupdict()
                        )
                    if l.startswith("A:"):
                        m = re.match("^A:(?P<name>[^:]+):(?P<type>[^:]+):(?P<help>[^:]+)(:(?P<nargs>[^:]+))?$", l)
                        if m is None:
                            raise click.UsageError(
                                "Expected format in {} is A:name:type:help[:nargs],"
                                " got {}".format(path, l))
                        arguments.append(
                            m.groupdict()
                        )
                    m = re.match("^N:(?P<help>[^:]+)$", l)
                    if m is not None:
                        remaining_args = m.group("help")
                cmdflowdepends = re.search('flowdepends: (.+)', out)
                if cmdflowdepends:
                    cmdflowdepends = cmdflowdepends.group(1).split(', ')
                else:
                    cmdflowdepends = []
            else:
                cmdflowdepends = []
                cmdhelp = "No help found... (the command is most likely broken)"
            process.wait()
        except Exception as e:
            LOGGER.warning("When loading command {}: {}".format(name, e))
            from click_project.overloads import on_command_loading_error
            on_command_loading_error()
            raise

        from click_project.decorators import command, argument, option, flag

        def external_command(**kwargs):
            from click_project.lib import call
            config.merge_settings()
            args = (
                [command_path]
                + get_settings_for_path("parameters", path)
            )
            env = {
                (config.main_command.path + "___" + key).upper(): (
                    str(value) if value else ""
                )
                for key, value in kwargs.items()
            }
            ctx = click.get_current_context()
            env[(config.main_command.path + "___PATH").upper()] = (
                ctx.command_path.replace(" ", "_").upper()
            )
            while ctx:
                env.update(
                    {
                        (ctx.command_path.replace(
                            " ", "_"
                        ) + "__" + key).upper(): (
                            (
                                " ".join(map(quote, value))
                                if type(value) is tuple
                                else
                                str(value) if value else ""
                            )
                        )
                        for key, value in ctx.params.items()
                    }
                )
                ctx = ctx.parent
            env[(config.main_command.path + "___CMD_OPTIND").upper()] = (
                str(len(config.command_line_settings["parameters"][path]))
            )
            env[(config.main_command.path + "___CMD_ARGS").upper()] = (
                " ".join(quote(a) for a in config.command_line_settings["parameters"][path])
            )
            env[(config.main_command.path + "___OPTIND").upper()] = (
                str(len(args[1:]))
            )
            env[(config.main_command.path + "___ARGS").upper()] = (
                " ".join(quote(a) for a in args[1:])
            )
            with updated_env(**env):
                call(
                    args
                )
        types = {
            "int": int,
            "float": float,
            "str": str,
        }
        for o in options:
            if "type" in o:
                if "." in o["type"]:
                    t = o["type"].split(".")
                    m = importlib.import_module(".".join(t[:-1]))
                    t = getattr(m, t[-1])
                else:
                    t = types[o["type"]]
            external_command = option(
                o["name"],
                help=o["help"],
                type=t or str,
            )(external_command)
        for a in arguments:
            if "type" in a:
                if "." in a["type"]:
                    t = a["type"].split(".")
                    m = importlib.import_module(".".join(t[:-1]))
                    t = getattr(m, t[-1])
                else:
                    t = types[a["type"]]
            external_command = argument(
                a["name"],
                help=a["help"],
                type=t or str,
                nargs=int(a["nargs"] or "1"),
            )(external_command)
        for f in flags:
            external_command = flag(
                f["name"],
                help=f["help"],
                default=f["default"] == "True",
            )(external_command)
        if remaining_args:
            external_command = argument('args', nargs=-1, help=remaining_args)(external_command)

        external_command = command(
            name=name,
            help=cmdhelp,
            short_help=cmdhelp.splitlines()[0] if cmdhelp else "",
            handle_dry_run=True,
            flowdepends=cmdflowdepends)(
                external_command
            )
        return external_command
