# =====================================================================
# auth-service / cli.py
# Comandos de administracion que se ejecutan desde la terminal:
#   python -m app.cli crear-admin --usuario angel
#   python -m app.cli crear-invitacion --dias 7 --nota "Farmacia Centro"
# =====================================================================

import argparse                                      # leer argumentos de la linea de comandos
import getpass                                       # pedir contrasenas sin mostrarlas en pantalla
import sys                                           # terminar con codigo de error

from app import core                                 # logica de seguridad


def main():
    analizador = argparse.ArgumentParser(description="Administracion de auth-service")
    sub = analizador.add_subparsers(dest="comando", required=True)   # subcomandos obligatorios

    admin = sub.add_parser("crear-admin", help="Crea una cuenta de administrador")
    admin.add_argument("--usuario", required=True)   # nombre del administrador

    inv = sub.add_parser("crear-invitacion", help="Genera un codigo de invitacion de un solo uso")
    inv.add_argument("--dias", type=int, default=7)  # vigencia en dias
    inv.add_argument("--nota", default=None)         # para quien es el codigo

    args = analizador.parse_args()                   # lee lo que escribio el usuario
    core.crear_tablas()                              # asegura que las tablas existan
    try:
        if args.comando == "crear-admin":
            clave = getpass.getpass("Contraseña: ")  # no se ve al escribirla
            repite = getpass.getpass("Repite la contraseña: ")
            if clave != repite:                      # deben coincidir
                sys.exit("Las contraseñas no coinciden")
            core.crear_admin(args.usuario, clave)    # crea la cuenta
            print(f"Administrador '{args.usuario.strip().lower()}' creado.")
        else:
            if not 1 <= args.dias <= 90:             # vigencia razonable
                sys.exit("--dias debe estar entre 1 y 90")
            codigo = core.crear_invitacion(args.nota, args.dias)
            print(f"\nCódigo de invitación (válido {args.dias} días, un solo uso):\n\n  {codigo}\n")
            print("Guárdalo ahora: no se puede volver a mostrar.")
    except core.ErrorAuth as e:                      # errores esperados: se muestran sin traza
        sys.exit(f"Error: {e.mensaje}")


if __name__ == "__main__":
    main()