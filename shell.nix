with import (fetchTarball https://github.com/NixOS/nixpkgs/archive/refs/tags/21.05.tar.gz) {};

let
  fast-enum = python38Packages.buildPythonPackage rec {
    pname = "fast-enum";
    version = "1.3.0";
    pyproject = true;
    src = python38Packages.fetchPypi {
      inherit pname version;
      hash = "sha256-TqW48mPuspW8qms3fux64ePfIHVN+9fq0aOqeKjw/A4=";
    };
  };
  python-bps-continued = python38Packages.buildPythonPackage rec {
    pname = "python-bps-continued";
    version = "7";
    pyproject = true;
    src = python38Packages.fetchPypi {
      inherit pname version;
      hash = "sha256-jGqmnHC7Jtk/AdvRW2xrVKAdPAEyyoLHC/2tvdwy0Z0=";
    };
  };
# in (python38.buildEnv.override {
in mkShell {
  packages = [
    (python38.withPackages (pkgs: with pkgs; [
      aioconsole
      numpy
      requests
      colorama
      websockets
      pyyaml
      fuzzywuzzy
      bsdiff4
      prompt_toolkit
      appdirs
      aenum
      tkinter
      fast-enum
      python-bps-continued
    ]))
  ];
}
