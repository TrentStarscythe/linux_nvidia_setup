#!/usr/bin/env python3
"""
Steam Driver Setup Script
Detects your Linux distribution and GPU, then installs the correct
graphics drivers needed for Steam (including kernel and login screen support).

Run with: sudo python3 steam_driver_setup.py
"""

import os
import sys
import subprocess
import shutil
import re
import json
from pathlib import Path


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def run(cmd, check=True, capture=True):
    """Run a shell command and return (returncode, stdout, stderr)."""
    result = subprocess.run(
        cmd, shell=True, text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )
    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()
    if check and result.returncode != 0:
        raise RuntimeError(f"Command failed: {cmd}\n{stderr}")
    return result.returncode, stdout, stderr


def banner(msg):
    width = 60
    print("\n" + "═" * width)
    print(f"  {msg}")
    print("═" * width)


def info(msg):   print(f"  [INFO]  {msg}")
def warn(msg):   print(f"  [WARN]  {msg}")
def success(msg):print(f"  [OK]    {msg}")
def error(msg):  print(f"  [ERROR] {msg}")


def require_root():
    if os.geteuid() != 0:
        error("This script must be run as root.  Try:  sudo python3 steam_driver_setup.py")
        sys.exit(1)


# ─────────────────────────────────────────────
# 1. Detect Linux distribution
# ─────────────────────────────────────────────

def detect_distro():
    """Return dict with id, name, version, id_like keys."""
    distro = {}
    os_release = Path("/etc/os-release")
    if os_release.exists():
        for line in os_release.read_text().splitlines():
            if "=" in line:
                k, _, v = line.partition("=")
                distro[k.strip().lower()] = v.strip().strip('"')
    if not distro:
        raise RuntimeError("Cannot read /etc/os-release — unsupported Linux system.")
    return distro


def distro_family(distro):
    """Return 'debian', 'rhel', 'arch', 'suse', or 'unknown'."""
    combined = (distro.get("id", "") + " " + distro.get("id_like", "")).lower()
    if any(x in combined for x in ("ubuntu", "debian", "mint", "pop", "elementary", "zorin")):
        return "debian"
    if any(x in combined for x in ("fedora", "rhel", "centos", "rocky", "alma", "nobara")):
        return "rhel"
    if any(x in combined for x in ("arch", "manjaro", "endeavouros", "garuda")):
        return "arch"
    if any(x in combined for x in ("opensuse", "suse")):
        return "suse"
    return "unknown"


# ─────────────────────────────────────────────
# 2. Detect GPU
# ─────────────────────────────────────────────

def detect_gpu():
    """Return list of dicts: [{vendor, name, pci_id}]"""
    gpus = []
    _, out, _ = run("lspci -nn 2>/dev/null | grep -Ei 'vga|3d|display'", check=False)
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        vendor = "unknown"
        if "nvidia" in line.lower():
            vendor = "nvidia"
        elif "advanced micro devices" in line.lower() or " amd " in line.lower() or "radeon" in line.lower():
            vendor = "amd"
        elif "intel" in line.lower():
            vendor = "intel"
        # Extract PCI id [xxxx:xxxx]
        pci_match = re.search(r'\[([0-9a-fA-F]{4}:[0-9a-fA-F]{4})\]', line)
        pci_id = pci_match.group(1) if pci_match else "unknown"
        gpus.append({"vendor": vendor, "name": line, "pci_id": pci_id})
    return gpus


# ─────────────────────────────────────────────
# 3. Package manager wrappers
# ─────────────────────────────────────────────

def install_kernel_headers(family):
    """Install the correct linux-headers package for the running kernel."""
    banner("Installing kernel headers")

    if family == "debian":
        # linux-headers-generic covers most Ubuntu/Debian installs.
        # linux-headers-$(uname -r) ensures an exact match for the running kernel.
        _, kernel, _ = run("uname -r")
        pkgs = [f"linux-headers-{kernel}", "linux-headers-generic"]
        # Try exact match first, fall back to generic
        if pkg_available(f"linux-headers-{kernel}", family):
            pkg_install([f"linux-headers-{kernel}"], family)
        elif pkg_available("linux-headers-generic", family):
            pkg_install(["linux-headers-generic"], family)
        else:
            warn(f"Could not find linux-headers for kernel {kernel} — driver may fail to build.")
            return

    elif family == "arch":
        # Arch uses linux-headers; linux-lts-headers for LTS kernel users
        _, kernel, _ = run("uname -r")
        if "lts" in kernel.lower():
            pkg_install(["linux-lts-headers"], family)
        elif "zen" in kernel.lower():
            pkg_install(["linux-zen-headers"], family)
        elif "hardened" in kernel.lower():
            pkg_install(["linux-hardened-headers"], family)
        else:
            pkg_install(["linux-headers"], family)

    elif family == "rhel":
        # kernel-devel matches the running kernel on Fedora/RHEL
        _, kernel, _ = run("uname -r")
        if pkg_available(f"kernel-devel-{kernel}", family):
            pkg_install([f"kernel-devel-{kernel}", "kernel-headers"], family)
        else:
            pkg_install(["kernel-devel", "kernel-headers"], family)

    elif family == "suse":
        pkg_install(["kernel-devel", "kernel-default-devel"], family)

    success("Kernel headers installed.")


def pkg_install(pkgs, family):
    pkg_str = " ".join(pkgs)
    cmds = {
        "debian": f"apt-get install -y {pkg_str}",
        "rhel":   f"dnf install -y {pkg_str}",
        "arch":   f"pacman -S --noconfirm {pkg_str}",
        "suse":   f"zypper install -y {pkg_str}",
    }
    cmd = cmds.get(family)
    if not cmd:
        raise RuntimeError(f"Unsupported distro family: {family}")
    info(f"Running: {cmd}")
    run(cmd, capture=False)


def pkg_update(family):
    cmds = {
        "debian": "apt-get update -y",
        "rhel":   "dnf makecache -y",
        "arch":   "pacman -Sy",
        "suse":   "zypper refresh",
    }
    cmd = cmds.get(family)
    if cmd:
        info("Refreshing package lists …")
        run(cmd, capture=False)


# ─────────────────────────────────────────────
# 4. Enable 32-bit / multilib support (needed for Steam)
# ─────────────────────────────────────────────

def enable_multilib(family, distro):
    if family == "debian":
        info("Enabling 32-bit architecture (i386) for Steam …")
        run("dpkg --add-architecture i386", check=False, capture=False)

    elif family == "arch":
        pacman_conf = Path("/etc/pacman.conf")
        content = pacman_conf.read_text()
        if "#[multilib]" in content:
            info("Enabling [multilib] repo in pacman.conf …")
            content = content.replace("#[multilib]", "[multilib]")
            content = content.replace("#Include = /etc/pacman.d/mirrorlist", "Include = /etc/pacman.d/mirrorlist", 1)
            pacman_conf.write_text(content)
        run("pacman -Sy", capture=False)

    elif family == "rhel":
        distro_id = distro.get("id", "")
        if "fedora" in distro_id:
            info("Enabling RPM Fusion (free + nonfree) for Fedora …")
            ver = distro.get("version_id", "")
            run(f"dnf install -y https://download1.rpmfusion.org/free/fedora/rpmfusion-free-release-{ver}.noarch.rpm", check=False, capture=False)
            run(f"dnf install -y https://download1.rpmfusion.org/nonfree/fedora/rpmfusion-nonfree-release-{ver}.noarch.rpm", check=False, capture=False)


# ─────────────────────────────────────────────
# 5. Driver install logic per GPU + distro
# ─────────────────────────────────────────────

def pkg_available(pkg, family):
    """Return True if a package exists in the repos (without installing it)."""
    check_cmds = {
        "debian": f"apt-cache show {pkg}",
        "rhel":   f"dnf info {pkg}",
        "arch":   f"pacman -Si {pkg}",
        "suse":   f"zypper info {pkg}",
    }
    cmd = check_cmds.get(family)
    if not cmd:
        return False
    rc, _, _ = run(cmd, check=False)
    return rc == 0


def install_nvidia(family, distro):
    banner("Installing NVIDIA drivers")

    # Kernel headers must be present before the driver module can build
    install_kernel_headers(family)

    # Common Vulkan / 32-bit libs needed by Steam on all distros
    vulkan_debian = ["libvulkan1", "libvulkan1:i386", "vulkan-tools"]
    vulkan_arch   = ["vulkan-icd-loader", "lib32-vulkan-icd-loader"]
    vulkan_rhel   = ["vulkan", "vulkan-loader.i686"]

    if family == "debian":
        distro_id = distro.get("id", "")

        # Pop!_OS has its own driver bundle
        if "pop" in distro_id:
            info("Pop!_OS detected — using system76-driver-nvidia …")
            pkg_install(["system76-driver-nvidia"] + vulkan_debian, family)

        else:
            # Prefer nvidia-open (Turing/RTX 20xx+), fall back to proprietary
            # metapackage, then fall back to a specific versioned package.
            if pkg_available("nvidia-open", family):
                info("nvidia-open package found — installing open kernel module …")
                try:
                    pkg_install(["nvidia-open",
                                 "nvidia-driver-libs:i386",
                                 "nvidia-settings"] + vulkan_debian, family)
                except RuntimeError:
                    warn("nvidia-open install failed; falling back to nvidia-driver …")
                    pkg_install(["nvidia-driver",
                                 "nvidia-driver-libs:i386",
                                 "nvidia-settings"] + vulkan_debian, family)
            elif pkg_available("nvidia-driver", family):
                info("Using nvidia-driver metapackage …")
                try:
                    pkg_install(["nvidia-driver",
                                 "nvidia-driver-libs:i386",
                                 "nvidia-settings"] + vulkan_debian, family)
                except RuntimeError:
                    warn("nvidia-driver failed; trying nvidia-driver-535 …")
                    pkg_install(["nvidia-driver-535",
                                 "libvulkan1", "libvulkan1:i386"], family)
            else:
                warn("No known NVIDIA package found in repos.")
                warn("Try: sudo ubuntu-drivers install  or visit https://www.nvidia.com/Download/index.aspx")

    elif family == "arch":
        # nvidia-open is the open module for Turing+; nvidia is closed/proprietary
        if pkg_available("nvidia-open", family):
            info("nvidia-open available — installing open kernel module …")
            pkg_install(["nvidia-open", "nvidia-utils", "lib32-nvidia-utils",
                         "nvidia-settings"] + vulkan_arch, family)
        else:
            info("Falling back to closed-source nvidia package …")
            pkg_install(["nvidia", "nvidia-utils", "lib32-nvidia-utils",
                         "nvidia-settings"] + vulkan_arch, family)
        info("Enabling nvidia-drm.modeset for login screen …")
        _write_modprobe_nvidia()
        _add_mkinitcpio_nvidia()

    elif family == "rhel":
        distro_id = distro.get("id", "")
        if "fedora" in distro_id:
            # akmod-nvidia-open is available in RPM Fusion for modern GPUs
            if pkg_available("akmod-nvidia-open", family):
                info("akmod-nvidia-open available — installing …")
                pkg_install(["akmod-nvidia-open",
                             "xorg-x11-drv-nvidia-cuda"] + vulkan_rhel, family)
            else:
                info("Using akmod-nvidia (closed-source) …")
                pkg_install(["akmod-nvidia",
                             "xorg-x11-drv-nvidia-cuda"] + vulkan_rhel, family)
        else:
            warn("RHEL/CentOS: install NVIDIA drivers manually from https://rpmfusion.org")

    elif family == "suse":
        # openSUSE uses G06 for current driver branch
        if pkg_available("nvidia-open-gfxG06", family):
            info("Using nvidia-open-gfxG06 (open module) …")
            pkg_install(["nvidia-open-gfxG06", "nvidia-glG06", "nvidia-computeG06"], family)
        else:
            info("Falling back to nvidia-video-G06 …")
            pkg_install(["nvidia-video-G06", "nvidia-gl-G06", "nvidia-compute-G06"], family)

    _configure_nvidia_kernel_params()
    success("NVIDIA driver installation complete.")


def install_amd(family, distro):
    banner("Installing AMD (Mesa / AMDGPU) drivers")

    if family == "debian":
        pkgs = [
            "mesa-vulkan-drivers",
            "mesa-vulkan-drivers:i386",
            "libvulkan1",
            "libvulkan1:i386",
            "vulkan-tools",
            "xserver-xorg-video-amdgpu",
            "firmware-amd-graphics",       # non-free on Debian; may need contrib/non-free repos
            "libgl1-mesa-dri",
            "libgl1-mesa-dri:i386",
        ]
        try:
            pkg_install(pkgs, family)
        except RuntimeError:
            warn("Some AMD packages failed (possibly need non-free repo). Check /etc/apt/sources.list.")

    elif family == "arch":
        pkg_install(["mesa", "lib32-mesa",
                     "xf86-video-amdgpu",
                     "vulkan-radeon", "lib32-vulkan-radeon",
                     "libva-mesa-driver", "lib32-libva-mesa-driver",
                     "mesa-vdpau", "lib32-mesa-vdpau"], family)

    elif family == "rhel":
        pkg_install(["mesa-dri-drivers", "mesa-vulkan-drivers",
                     "xorg-x11-drv-amdgpu", "vulkan-loader"], family)

    elif family == "suse":
        pkg_install(["Mesa", "Mesa-libGL1", "xf86-video-amdgpu"], family)

    success("AMD driver installation complete.")


def install_intel(family, distro):
    banner("Installing Intel (i915 / xe) drivers")

    if family == "debian":
        pkg_install(["mesa-vulkan-drivers",
                     "mesa-vulkan-drivers:i386",
                     "libvulkan1", "libvulkan1:i386",
                     "intel-media-va-driver",
                     "libgl1-mesa-dri", "libgl1-mesa-dri:i386",
                     "vulkan-tools"], family)

    elif family == "arch":
        pkg_install(["mesa", "lib32-mesa",
                     "vulkan-intel", "lib32-vulkan-intel",
                     "intel-media-driver", "libva-intel-driver"], family)

    elif family == "rhel":
        pkg_install(["mesa-dri-drivers", "mesa-vulkan-drivers",
                     "intel-media-driver", "vulkan-loader"], family)

    elif family == "suse":
        pkg_install(["Mesa", "Mesa-libGL1", "intel-media-driver"], family)

    success("Intel driver installation complete.")


# ─────────────────────────────────────────────
# 6. Kernel / login screen configuration
# ─────────────────────────────────────────────

def _write_modprobe_nvidia():
    """Tell the kernel to enable DRM modesetting for NVIDIA."""
    conf = Path("/etc/modprobe.d/nvidia-drm.conf")
    conf.write_text("options nvidia-drm modeset=1\n")
    info(f"Written {conf}")


def _add_mkinitcpio_nvidia():
    """Add nvidia modules to mkinitcpio on Arch-based systems."""
    mkinit = Path("/etc/mkinitcpio.conf")
    if not mkinit.exists():
        return
    content = mkinit.read_text()
    modules_line = re.search(r'^MODULES=\((.*?)\)', content, re.MULTILINE)
    if modules_line:
        existing = modules_line.group(1)
        needed = ["nvidia", "nvidia_modeset", "nvidia_uvm", "nvidia_drm"]
        to_add = [m for m in needed if m not in existing]
        if to_add:
            new_mods = (existing + " " + " ".join(to_add)).strip()
            content = content.replace(modules_line.group(0), f"MODULES=({new_mods})")
            mkinit.write_text(content)
            info("Updated mkinitcpio.conf MODULES.")
            run("mkinitcpio -P", capture=False, check=False)


def _configure_nvidia_kernel_params():
    """Enable nvidia-drm.modeset=1 via modprobe.d (works for GDM/SDDM/LightDM)."""
    _write_modprobe_nvidia()
    # Also ensure the drm module loads early for display managers
    modules_load = Path("/etc/modules-load.d/nvidia-drm.conf")
    modules_load.write_text("nvidia-drm\n")
    info(f"Written {modules_load}")


def configure_grub_nvidia():
    """Optionally patch GRUB to set nvidia-drm.modeset=1 on the kernel cmdline."""
    grub_default = Path("/etc/default/grub")
    if not grub_default.exists():
        return
    content = grub_default.read_text()
    param = "nvidia-drm.modeset=1"
    if param in content:
        info("GRUB already has nvidia-drm.modeset=1.")
        return
    content = re.sub(
        r'(GRUB_CMDLINE_LINUX_DEFAULT="[^"]*)"',
        lambda m: m.group(0).replace('"', f' {param}"', 1),
        content,
    )
    grub_default.write_text(content)
    info("Patched /etc/default/grub with nvidia-drm.modeset=1")
    # Update GRUB
    for grub_cmd in ["update-grub", "grub2-mkconfig -o /boot/grub2/grub.cfg",
                     "grub-mkconfig -o /boot/grub/grub.cfg"]:
        if shutil.which(grub_cmd.split()[0]):
            run(grub_cmd, check=False, capture=False)
            break


# ─────────────────────────────────────────────
# 7. Steam installation
# ─────────────────────────────────────────────

def install_steam(family):
    banner("Installing Steam")

    if family == "debian":
        pkg_install(["steam-installer"], family)

    elif family == "arch":
        pkg_install(["steam"], family)

    elif family == "rhel":
        pkg_install(["steam"], family)   # needs RPM Fusion nonfree

    elif family == "suse":
        warn("Steam on openSUSE: install via Flathub (flatpak install flathub com.valvesoftware.Steam)")
        return

    success("Steam installed.")


# ─────────────────────────────────────────────
# 8. Main
# ─────────────────────────────────────────────

def main():
    require_root()

    # ── Detect system ──────────────────────────────────────
    banner("Detecting system")

    distro = detect_distro()
    family = distro_family(distro)

    info(f"Distribution : {distro.get('name', 'unknown')}  ({distro.get('version_id', '?')})")
    info(f"Distro family: {family}")

    if family == "unknown":
        error("Unsupported distribution.  Please install drivers manually.")
        sys.exit(1)

    gpus = detect_gpu()
    if not gpus:
        warn("No GPU detected via lspci.  Ensure pciutils is installed.")
        sys.exit(1)

    print()
    for i, gpu in enumerate(gpus, 1):
        info(f"GPU {i}: [{gpu['vendor'].upper()}]  {gpu['name']}")

    # Pick primary GPU vendor (prefer dedicated GPU if multiple)
    vendor_priority = {"nvidia": 3, "amd": 2, "intel": 1, "unknown": 0}
    primary = max(gpus, key=lambda g: vendor_priority.get(g["vendor"], 0))
    vendor = primary["vendor"]
    info(f"Primary GPU vendor: {vendor.upper()}")

    # ── Update packages ────────────────────────────────────
    banner("Updating package lists")
    pkg_update(family)
    enable_multilib(family, distro)
    pkg_update(family)   # refresh again after multilib / RPM Fusion

    # ── Install drivers ────────────────────────────────────
    if vendor == "nvidia":
        install_nvidia(family, distro)
        configure_grub_nvidia()
    elif vendor == "amd":
        install_amd(family, distro)
    elif vendor == "intel":
        install_intel(family, distro)
    else:
        warn("Unknown GPU vendor — skipping driver installation.")

    # ── Install Steam ──────────────────────────────────────
    install_steam(family)

    # ── Summary ────────────────────────────────────────────
    banner("Setup complete")
    print()
    print("  A reboot is required to load the new kernel modules.")
    print("  After rebooting, launch Steam and enable Proton under:")
    print("  Steam → Settings → Compatibility → Enable Steam Play")
    print()

    reboot = input("  Reboot now? [y/N]: ").strip().lower()
    if reboot == "y":
        run("reboot", capture=False)
    else:
        info("Reboot skipped.  Run 'sudo reboot' when ready.")


if __name__ == "__main__":
    main()