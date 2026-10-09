{
  description = "IRIX 6.5 cross toolchain (GCC 16.2 IRIX series, pdaxrom lineage)";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" ];

      forAllSystems = f:
        nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});

      mkEnv = pkgs:
        let
          # GCC 13 matches the host compiler the pdaxrom recipe is proven on;
          # GCC 14+ turns the legacy binutils 2.20.1 warnings into errors.
          hostGcc = pkgs.gcc13;

          # GCC's build-time prerequisites, merged into one prefix so that
          # --with-gmp/--with-mpfr/--with-mpc/--with-isl all point at it.
          hostPrereqs = pkgs.symlinkJoin {
            name = "irix-cross-host-prereqs";
            paths = with pkgs; [
              gmp.dev gmp.out
              mpfr.dev mpfr.out
              libmpc
              isl
            ];
          };

          hostTools = with pkgs; [
            hostGcc
            binutils
            gnumake
            bison
            flex
            gperf
            autoconf
            automake
            perl
            texinfo
            gawk
            m4
            curl
            cacert
            xz
            bzip2
            gzip
            patch
            diffutils
            file
            git
          ];

          env = {
            CC = "${hostGcc}/bin/gcc";
            CXX = "${hostGcc}/bin/g++";
            GMP_PREFIX = "${hostPrereqs}";
            MPFR_PREFIX = "${hostPrereqs}";
            MPC_PREFIX = "${hostPrereqs}";
            ISL_PREFIX = "${hostPrereqs}";
            SSL_CERT_FILE = "${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt";
          };
        in
        {
          inherit hostTools env;
          build = pkgs.writeShellApplication {
            name = "irix-toolchain";
            runtimeInputs = hostTools;
            text = ''
              export CC=${env.CC}
              export CXX=${env.CXX}
              export GMP_PREFIX=${env.GMP_PREFIX}
              export MPFR_PREFIX=${env.MPFR_PREFIX}
              export MPC_PREFIX=${env.MPC_PREFIX}
              export ISL_PREFIX=${env.ISL_PREFIX}
              export SSL_CERT_FILE=${env.SSL_CERT_FILE}
              export IRIX_WORK_ROOT="''${IRIX_WORK_ROOT:-$PWD/.scratch}"
              exec ${pkgs.bash}/bin/bash ${self}/scripts/build-toolchain.sh "$@"
            '';
          };
        };
    in
    {
      devShells = forAllSystems (pkgs:
        let
          inherit (mkEnv pkgs) hostTools env;

          # Host libraries the emulator's Rust dependencies probe for at build
          # time (cpal -> alsa, winit/glutin -> wayland/xkbcommon). Kept in a
          # separate shell so the toolchain build is unaffected by them.
          rigTools = with pkgs; [
            pkg-config
            alsa-lib
            wayland
            wayland-protocols
            libxkbcommon
            linuxHeaders
            rustup
            python3
          ];
        in
        {
          default = pkgs.mkShell {
            # The pdaxrom recipe is built without distro hardening; -Werror
            # from hardening flags breaks the legacy GCC sources.
            hardeningDisable = [ "all" ];

            packages = hostTools;
            inherit (env) CC CXX GMP_PREFIX MPFR_PREFIX MPC_PREFIX ISL_PREFIX
              SSL_CERT_FILE;

            shellHook = ''
              echo "IRIX 6.5 cross-toolchain devshell"
              echo "Build the toolchain with: scripts/build-toolchain.sh"
            '';
          };

          rig = pkgs.mkShell {
            hardeningDisable = [ "all" ];

            packages = hostTools ++ rigTools;

            shellHook = ''
              # v4l2-sys-mit runs bindgen, which dlopens libclang and parses
              # the kernel uapi headers directly (not through the compiler).
              export LIBCLANG_PATH="${pkgs.llvmPackages.libclang.lib}/lib"
              export BINDGEN_EXTRA_CLANG_ARGS="-isystem ${pkgs.glibc.dev}/include -isystem ${pkgs.linuxHeaders}/include"
              echo "IRIX rig devshell (emulator host)"
              echo "Build the rig with: scripts/rig/build-iris.sh"
            '';
          };
        });

      packages = forAllSystems (pkgs: {
        default = (mkEnv pkgs).build;
      });

      apps = forAllSystems (pkgs: {
        default = {
          type = "app";
          program = "${(mkEnv pkgs).build}/bin/irix-toolchain";
        };
      });
    };
}
