#!/usr/bin/env python

# This example shows how to create a more complex Mininet topology
# with multiple clients, servers, and switches, and connect it to a Ryu controller.

from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch
from mininet.cli import CLI
from mininet.log import setLogLevel, info

def complexNet():

    "Create a more complex network and add nodes to it."

    # Mininet'i bir kontrolcüye bağlamak için başlatıyoruz
    # Ryu 127.0.0.1:6653 adresinde çalışıyor.
    net = Mininet( controller=None, waitConnected=True )

    info( '*** Adding controller\n' )
    net.addController(name='c0', controller=RemoteController, ip="127.0.0.1", port=6653)
    # RYU CONTROLLER ON

    info( '*** Adding hosts (clients and servers)\n' )
    # İstemciler
    c1 = net.addHost( 'c1', ip='10.0.0.1/24' )
    c2 = net.addHost( 'c2', ip='10.0.0.2/24' )

    # Video Sunucuları
    # Bu sunucularda HTTP sunucularını çalıştıracağız (şimdilik bu Mininet içinde değil, WSL'de)
    sva = net.addHost( 'sva', ip='10.0.0.10/24' ) # Video Server A
    svb = net.addHost( 'svb', ip='10.0.0.11/24' ) # Video Server B (Yedek veya İkinci kaynak)


    info( '*** Adding switches\n' )
    s1 = net.addSwitch( 's1', cls=OVSSwitch, protocols='OpenFlow13' ) # Anahtar 1
    s2 = net.addSwitch( 's2', cls=OVSSwitch, protocols='OpenFlow13' ) # Anahtar 2
    s3 = net.addSwitch( 's3', cls=OVSSwitch, protocols='OpenFlow13' ) # Anahtar 3

    info( '*** Creating links\n' )
    # İstemci Bağlantıları
    net.addLink( c1, s1 )
    net.addLink( c2, s1 )

    # Sunucu Bağlantıları
    net.addLink( sva, s3 )
    net.addLink( svb, s3 )

    # Anahtar Bağlantıları (Omurga)
    net.addLink( s1, s2 )
    net.addLink( s2, s3 )
    # Not: Daha fazla anahtar ekleyerek daha karmaşık bir omurga oluşturabilirsiniz.

    info( '*** Starting network\n')
    net.start()

    # Önceki HTTP sunucusu ve tarayıcı başlatma komutları yorumlu kalacak.
    # Bunları WSL terminalinde ve Windows tarayıcınızda manuel olarak yöneteceğiz.
    # h2.cmd('xterm -e python3 -m http.server &')
    # input()
    # h1.cmd('xterm -e google-chrome --kiosk --no-sandbox --autoplay-policy=no-user-gesture-required --app=http://10.0.0.2:8000/index.html &')

    # Ağ kurulduktan sonra CLI'yı başlatırız
    info( '*** Running CLI\n' )
    CLI( net )

    info( '*** Stopping network\n' )
    net.stop()


if __name__ == '__main__':
    setLogLevel( 'info' )
    complexNet() # emptyNet yerine complexNet fonksiyonunu çağırın
