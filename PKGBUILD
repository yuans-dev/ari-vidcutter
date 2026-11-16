
pkgname=ari-vidcutter
pkgver=0.1.0
pkgrel=1
pkgdesc='Simple video trimmer built with PySide6 and ffmpeg'
arch=('any')
url='https://github.com/yuans-dev/ari-vidcutter'
license=('GPL-3.0-or-later')
depends=('python' 'pyside6' 'qt6-multimedia' 'ffmpeg')
optdepends=('kvantum: Kvantum theme engine')
source=('ari.py'
        'ari-vidcutter.desktop'
        'ari-vidcutter-trim.desktop')
sha256sums=('SKIP' 'SKIP' 'SKIP')

package() {
    install -Dm755 "$srcdir/ari.py" "$pkgdir/usr/bin/ari-vidcutter"
    install -Dm644 "$srcdir/ari-vidcutter.desktop" "$pkgdir/usr/share/applications/ari-vidcutter.desktop"
    install -Dm644 "$srcdir/ari-vidcutter-trim.desktop" "$pkgdir/usr/share/kio/servicemenus/ari-vidcutter-trim.desktop"
}
