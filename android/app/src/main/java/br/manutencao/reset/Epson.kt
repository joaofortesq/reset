package br.manutencao.reset

import java.util.Calendar

class LeituraRecusada(msg: String) : Exception(msg)
class GravacaoRecusada(msg: String, var gravados: Int = 0) : Exception(msg)

data class Leitura(val bytes: List<Int>, val valor: Long, val percentual: Double?)

/** Leitura/gravação da EEPROM pelo comando EPSON-CTRL "||" (igual ao programa do Windows). */
class Epson(private val canal: CanalCtrl, private val p: Perfil) {

    private fun ctrl(cmd: String, payload: ByteArray): ByteArray {
        val c = cmd.toByteArray(Charsets.US_ASCII)
        return c + byteArrayOf((payload.size and 0xFF).toByte(), (payload.size shr 8).toByte()) + payload
    }

    fun ler(addr: Int): Int {
        val pl = byteArrayOf(p.readKey[0].toByte(), p.readKey[1].toByte(), 0x41, 0xBE.toByte(), 0xA0.toByte(),
            (addr and 0xFF).toByte(), (addr shr 8).toByte())
        val txt = String(canal.transact(ctrl("||", pl)), Charsets.ISO_8859_1)
        val m = Regex("EE:([0-9A-Fa-f]{4})([0-9A-Fa-f]{2})").find(txt)
            ?: throw LeituraRecusada("Leitura recusada no endereço $addr: $txt")
        if (m.groupValues[1].toInt(16) != addr) throw LeituraRecusada("Endereço devolvido difere de $addr: $txt")
        return m.groupValues[2].toInt(16)
    }

    fun gravar(addr: Int, valor: Int) {
        val wk = p.writeKey.toByteArray(Charsets.US_ASCII).map { if (it.toInt() == 0) 0 else it + 1 }
        val pl = byteArrayOf(p.readKey[0].toByte(), p.readKey[1].toByte(), 0x42, 0xBD.toByte(), 0x21,
            (addr and 0xFF).toByte(), (addr shr 8).toByte(), valor.toByte()) + ByteArray(wk.size) { wk[it].toByte() }
        val r = String(canal.transact(ctrl("||", pl)), Charsets.ISO_8859_1)
        if (!r.contains(":OK;")) throw GravacaoRecusada("Gravação recusada no endereço $addr: $r")
        val lido = ler(addr)  // alguns firmwares respondem OK sem gravar
        if (lido != valor) throw GravacaoRecusada("Endereço $addr: gravado $valor, releitura $lido")
    }

    fun contadores(): LinkedHashMap<String, Leitura> {
        val res = LinkedHashMap<String, Leitura>()
        for (c in p.contadores) {
            val bs = c.enderecos.map { ler(it) }
            var v = 0L
            for (b in bs.reversed()) v = (v shl 8) or b.toLong()
            val pct = c.divisor?.let { Math.round(v / it * 100.0) / 100.0 }
            res[c.nome] = Leitura(bs, v, pct)
        }
        return res
    }

    fun backup(): Map<Int, Int> {
        val ends = ((p.reset?.keys ?: emptySet()) + p.commit.keys).sorted()
        return ends.associateWith { ler(it) }
    }

    fun resetar(progresso: (Int, Int) -> Unit) {
        val itens = p.reset?.entries?.toList() ?: return
        itens.forEachIndexed { i, (a, v) ->
            try {
                gravar(a, v)
            } catch (e: GravacaoRecusada) {
                e.gravados = i; throw e
            } catch (e: Exception) {
                throw GravacaoRecusada("Falha no endereço $a: ${e.message}", i)
            }
            progresso(i + 1, itens.size)
        }
        for ((a, mask) in p.commit) {  // byte de confirmação (plataformas novas)
            val atual = ler(a)
            val novo = atual and mask
            if (novo != atual) gravar(a, novo)
        }
    }
}

/** Sequências do "remote mode" — cópia do que o driver oficial Epson envia (capturado da fila do Windows). */
object Comandos {
    private val EXIT_PACKET = byteArrayOf(0, 0, 0, 0x1b, 0x01) + "@EJL 1284.4\n@EJL     \n".toByteArray(Charsets.US_ASCII)
    private val INIT = byteArrayOf(0x1b, 0x40)
    private val REMOTE1 = byteArrayOf(0x1b, 0x28, 0x52, 0x08, 0x00, 0x00) + "REMOTE1".toByteArray(Charsets.US_ASCII)
    private val REMOTE_ON = INIT + INIT + REMOTE1
    private val REMOTE_OFF = byteArrayOf(0x1b, 0, 0, 0)

    private fun rc(cmd: String, args: ByteArray): ByteArray =
        cmd.toByteArray(Charsets.US_ASCII) + byteArrayOf((args.size and 0xFF).toByte(), (args.size shr 8).toByte()) + args

    private fun ti(): ByteArray {
        val c = Calendar.getInstance()
        val ano = c.get(Calendar.YEAR)
        return rc("TI", byteArrayOf(0, (ano shr 8).toByte(), ano.toByte(), (c.get(Calendar.MONTH) + 1).toByte(),
            c.get(Calendar.DAY_OF_MONTH).toByte(), c.get(Calendar.HOUR_OF_DAY).toByte(),
            c.get(Calendar.MINUTE).toByte(), c.get(Calendar.SECOND).toByte()))
    }

    private val JOB_END = rc("JE", byteArrayOf(0))

    fun testeJatos(): ByteArray =
        EXIT_PACKET + REMOTE_ON + ti() + rc("JS", byteArrayOf(0, 0, 0, 0)) + rc("NC", byteArrayOf(0, 0)) + REMOTE_OFF +
            "\r\n\r\n".toByteArray(Charsets.US_ASCII) +
            REMOTE1 + rc("VI", byteArrayOf(0, 0)) + rc("LD", ByteArray(0)) + REMOTE_OFF +
            byteArrayOf(0x0c) +
            REMOTE_ON + JOB_END + REMOTE_OFF

    fun limpeza(intensa: Boolean): ByteArray {
        val grupo = if (intensa) 0x10 else 0x00  // bit 0x10 = power cleaning
        return EXIT_PACKET + REMOTE_ON + ti() + rc("CH", byteArrayOf(0, grupo.toByte())) + REMOTE_OFF +
            REMOTE_ON + JOB_END + REMOTE_OFF
    }
}
