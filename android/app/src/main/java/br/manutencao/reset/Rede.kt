package br.manutencao.reset

import android.content.Context
import android.net.ConnectivityManager
import android.net.wifi.WifiManager
import java.io.ByteArrayOutputStream
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.Inet4Address
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.Socket
import java.net.SocketTimeoutException
import kotlin.random.Random

/**
 * Acesso pela rede (Wi-Fi): SNMP v1, comunidade "public" — igual ao programa do Windows.
 * O comando EPSON-CTRL vai dentro do OID 1.3.6.1.4.1.1248.1.2.2.44.1.1.2.1.<bytes>.
 */
open class SnmpErro(msg: String) : Exception(msg)
class SnmpSemObjeto(msg: String) : SnmpErro(msg)

object Snmp {
    const val OID_CTRL = "1.3.6.1.4.1.1248.1.2.2.44.1.1.2.1"
    const val OID_DEVICE_ID = "1.3.6.1.4.1.1248.1.2.2.1.1.1.1.1"
    private val COMUNIDADE = "public".toByteArray(Charsets.US_ASCII)

    private fun len(n: Int): ByteArray = when {
        n < 0x80 -> byteArrayOf(n.toByte())
        n < 0x100 -> byteArrayOf(0x81.toByte(), n.toByte())
        else -> byteArrayOf(0x82.toByte(), (n shr 8).toByte(), n.toByte())
    }

    private fun tlv(tag: Int, v: ByteArray) = byteArrayOf(tag.toByte()) + len(v.size) + v

    private fun inteiro(v: Int): ByteArray {
        val out = ByteArrayOutputStream()
        var bytes = byteArrayOf((v shr 24).toByte(), (v shr 16).toByte(), (v shr 8).toByte(), v.toByte())
        while (bytes.size > 1 && ((bytes[0].toInt() == 0 && bytes[1] >= 0) || (bytes[0].toInt() == -1 && bytes[1] < 0)))
            bytes = bytes.copyOfRange(1, bytes.size)
        out.write(bytes)
        return tlv(0x02, out.toByteArray())
    }

    private fun oid(s: String): ByteArray {
        val p = s.split(".").map { it.toLong() }
        val out = ByteArrayOutputStream()
        out.write((40 * p[0] + p[1]).toInt())
        for (n0 in p.drop(2)) {
            var n = n0
            val enc = ArrayDeque<Int>()
            enc.addFirst((n and 0x7F).toInt())
            n = n shr 7
            while (n > 0) { enc.addFirst(((n and 0x7F) or 0x80).toInt()); n = n shr 7 }
            enc.forEach { out.write(it) }
        }
        return tlv(0x06, out.toByteArray())
    }

    fun montarGet(o: String, reqId: Int): ByteArray {
        val vb = tlv(0x30, tlv(0x30, oid(o) + byteArrayOf(0x05, 0x00)))
        val pdu = tlv(0xA0, inteiro(reqId) + inteiro(0) + inteiro(0) + vb)
        return tlv(0x30, inteiro(0) + tlv(0x04, COMUNIDADE) + pdu)
    }

    private class Tlv(val tag: Int, val v: ByteArray, val fim: Int)

    private fun lerTlv(b: ByteArray, i0: Int): Tlv {
        var i = i0
        val tag = b[i].toInt() and 0xFF
        var n = b[i + 1].toInt() and 0xFF
        i += 2
        if (n and 0x80 != 0) {
            val k = n and 0x7F
            n = 0
            repeat(k) { n = (n shl 8) or (b[i].toInt() and 0xFF); i++ }
        }
        return Tlv(tag, b.copyOfRange(i, i + n), i + n)
    }

    private fun intDe(b: ByteArray): Int {
        var v = if (b.isNotEmpty() && b[0] < 0) -1 else 0
        for (x in b) v = (v shl 8) or (x.toInt() and 0xFF)
        return v
    }

    /** Devolve (reqId, erro, valor ou null). */
    fun lerResposta(b: ByteArray): Triple<Int, Int, ByteArray?> {
        val msg = lerTlv(b, 0).v
        var t = lerTlv(msg, 0)              // versão
        t = lerTlv(msg, t.fim)              // comunidade
        val pdu = lerTlv(msg, t.fim)
        if (pdu.tag != 0xA2) throw SnmpErro("Resposta SNMP inesperada")
        val rid = lerTlv(pdu.v, 0)
        val err = lerTlv(pdu.v, rid.fim)
        val idx = lerTlv(pdu.v, err.fim)
        val vbl = lerTlv(pdu.v, idx.fim)
        val vb = lerTlv(vbl.v, 0)
        val o = lerTlv(vb.v, 0)
        val valor = lerTlv(vb.v, o.fim)
        val nulo = valor.tag in setOf(0x80, 0x81, 0x82, 0x05)
        return Triple(intDe(rid.v), intDe(err.v), if (nulo) null else valor.v)
    }

    fun get(ip: String, o: String, timeoutMs: Int = 2000, tentativas: Int = 2): ByteArray? {
        val rid = Random.nextInt(1, 0x7FFFFF)
        val pkt = montarGet(o, rid)
        DatagramSocket().use { s ->
            s.soTimeout = timeoutMs
            val destino = InetAddress.getByName(ip)
            repeat(tentativas) {
                s.send(DatagramPacket(pkt, pkt.size, destino, 161))
                val fim = System.currentTimeMillis() + timeoutMs
                while (System.currentTimeMillis() < fim) {
                    val buf = ByteArray(65535)
                    val dp = DatagramPacket(buf, buf.size)
                    try { s.receive(dp) } catch (_: SocketTimeoutException) { break }
                    val r = try { lerResposta(buf.copyOf(dp.length)) } catch (_: Exception) { continue }
                    if (r.first == rid) {
                        if (r.second == 2) throw SnmpSemObjeto("OID desconhecido em $ip")
                        if (r.second != 0) throw SnmpErro("A impressora recusou a consulta SNMP (erro ${r.second})")
                        return r.third
                    }
                }
            }
        }
        throw SnmpErro("Sem resposta SNMP de $ip")
    }

    fun deviceId(ip: String): String = get(ip, OID_DEVICE_ID)?.toString(Charsets.ISO_8859_1) ?: ""

    /** Endereços de broadcast das redes IPv4 do aparelho. */
    private fun broadcasts(ctx: Context): Set<InetAddress> {
        val alvos = mutableSetOf<InetAddress>(InetAddress.getByName("255.255.255.255"))
        try {
            val cm = ctx.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
            for (rede in cm.allNetworks) {
                val lp = cm.getLinkProperties(rede) ?: continue
                for (la in lp.linkAddresses) {
                    val a = la.address as? Inet4Address ?: continue
                    if (a.isLoopbackAddress) continue
                    val ipi = a.address.fold(0) { acc, b -> (acc shl 8) or (b.toInt() and 0xFF) }
                    val mask = if (la.prefixLength == 0) 0 else -1 shl (32 - la.prefixLength)
                    val bc = ipi or mask.inv()
                    alvos += InetAddress.getByAddress(byteArrayOf((bc shr 24).toByte(), (bc shr 16).toByte(), (bc shr 8).toByte(), bc.toByte()))
                }
            }
        } catch (_: Exception) {
        }
        return alvos
    }

    /** Procura Epson na rede: devolve lista (ip, deviceId). */
    fun descobrir(ctx: Context, tempoMs: Long = 2500): List<Pair<String, String>> {
        val wifi = ctx.applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
        val trava = wifi.createMulticastLock("descoberta-epson").apply { setReferenceCounted(false) }
        val achadas = linkedMapOf<String, String>()
        try {
            trava.acquire()
            val rid = Random.nextInt(1, 0x7FFFFF)
            val pkt = montarGet(OID_DEVICE_ID, rid)
            DatagramSocket().use { s ->
                s.broadcast = true
                s.soTimeout = 300
                for (alvo in broadcasts(ctx)) {
                    try { s.send(DatagramPacket(pkt, pkt.size, alvo, 161)) } catch (_: Exception) { }
                }
                val fim = System.currentTimeMillis() + tempoMs
                while (System.currentTimeMillis() < fim) {
                    val buf = ByteArray(65535)
                    val dp = DatagramPacket(buf, buf.size)
                    try { s.receive(dp) } catch (_: SocketTimeoutException) { continue }
                    val r = try { lerResposta(buf.copyOf(dp.length)) } catch (_: Exception) { continue }
                    val v = r.third ?: continue
                    val txt = v.toString(Charsets.ISO_8859_1)
                    if (r.first == rid && r.second == 0 && txt.uppercase().contains("EPSON"))
                        achadas[dp.address.hostAddress ?: continue] = txt
                }
            }
        } finally {
            if (trava.isHeld) trava.release()
        }
        return achadas.toList()
    }

    fun enviarRaw9100(ip: String, dados: ByteArray) {
        Socket().use { s ->
            s.connect(InetSocketAddress(ip, 9100), 10000)
            s.getOutputStream().apply { write(dados); flush() }
            s.shutdownOutput()
            Thread.sleep(500)
        }
    }
}

class RedeCanal(private val ip: String) : CanalCtrl {
    override fun transact(payload: ByteArray): ByteArray {
        val o = Snmp.OID_CTRL + "." + payload.joinToString(".") { (it.toInt() and 0xFF).toString() }
        return Snmp.get(ip, o, timeoutMs = 3000) ?: throw SnmpErro("Comando não suportado pela impressora na rede")
    }
}
