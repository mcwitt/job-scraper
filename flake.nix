{
  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixpkgs-unstable";
    systems.url = "github:nix-systems/default";
    git-hooks.url = "github:cachix/git-hooks.nix";
  };

  outputs =
    {
      self,
      systems,
      nixpkgs,
      ...
    }@inputs:
    let
      forEachSystem = nixpkgs.lib.genAttrs (import systems);
      pythonFor =
        system:
        nixpkgs.legacyPackages.${system}.python3.withPackages (
          ps: with ps; [
            anthropic
            beautifulsoup4
            dacite
            httpx
            jinja2
            lxml
            typer
          ]
        );
    in
    {
      # Run the hooks with `nix fmt`.
      formatter = forEachSystem (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          config = self.checks.${system}.pre-commit-check.config;
          inherit (config) package configFile;
          script = ''
            ${pkgs.lib.getExe package} run --all-files --config ${configFile}
          '';
        in
        pkgs.writeShellScriptBin "pre-commit-run" script
      );

      # Run the hooks in a sandbox with `nix flake check`.
      # Read-only filesystem and no internet access.
      checks = forEachSystem (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
        in
        {
          pre-commit-check = inputs.git-hooks.lib.${system}.run {
            src = ./.;
            hooks = {
              nixfmt.enable = true;
              pyrefly = (
                { config, lib, ... }:
                {
                  enable = true;
                  package = pkgs.pyrefly;
                  entry = "${lib.getExe config.package} check --python-interpreter-path ${pythonFor system}/bin/python3";
                  types = [ "python" ];
                }
              );
              ruff.enable = true;
            };
          };
        }
      );

      packages = forEachSystem (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          python = pythonFor system;
        in
        {
          default =
            let
              wrapper = pkgs.writeShellScript "job-scraper" ''
                export PYTHONPATH="@lib@:''${PYTHONPATH:+:$PYTHONPATH}"
                exec ${python}/bin/python -m job_scraper.main "$@"
              '';
            in
            pkgs.stdenvNoCC.mkDerivation {
              name = "job-scraper";
              src = ./job_scraper;
              dontBuild = true;
              installPhase = ''
                mkdir -p $out/lib/job_scraper
                cp -r $src/* $out/lib/job_scraper/
                mkdir -p $out/bin
                substitute ${wrapper} $out/bin/job-scraper \
                  --replace-fail '@lib@' "$out/lib"
                chmod +x $out/bin/job-scraper
              '';
            };
        }
      );

      nixosModules.default = import ./nix/module.nix self;

      # Enter a development shell with `nix develop`.
      # The hooks will be installed automatically.
      # Or run pre-commit manually with `nix develop -c pre-commit run --all-files`
      devShells = forEachSystem (system: {
        default =
          let
            pkgs = nixpkgs.legacyPackages.${system};
            inherit (self.checks.${system}.pre-commit-check) shellHook enabledPackages;
          in
          pkgs.mkShell {
            inherit shellHook;
            packages = [ (pythonFor system) ];
            buildInputs = enabledPackages;
          };
      });
    };
}
