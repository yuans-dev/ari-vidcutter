# Maintainer: Your Name <you@example.com>
pkgname=ari-vidcutter
pkgver=0.1.0
pkgrel=1
pkgdesc='Simple video trimmer built with PySide6 and ffmpeg'
arch=('any')
url='https://github.com/yuans-dev/ari-vidcutter'  # CHANGE ME: project page or repository
license=('GPL-3.0-or-later')   # CHANGE ME: use the license you actually release under
depends=('python' 'pyside6' 'qt6-multimedia' 'ffmpeg')
optdepends=('kvantum: Kvantum theme engine')
source=('ari.py'
        'ari.desktop')
sha256sums=('SKIP'
            'SKIP')

package() {
    install -Dm755 "$srcdir/ari.py" "$pkgdir/usr/bin/ari-vidcutter"
    install -Dm644 "$srcdir/ari.desktop" "$pkgdir/usr/share/applications/ari.desktop"
}
