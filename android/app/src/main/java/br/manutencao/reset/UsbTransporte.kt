package br.manutencao.reset

import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.hardware.usb.UsbConstants
import android.hardware.usb.UsbDevice
import android.hardware.usb.UsbDeviceConnection
import android.hardware.usb.UsbEndpoint
import android.hardware.usb.UsbInterface
import android.hardware.usb.UsbManager
import android.os.Build
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlin.coroutines.resume

const val VENDOR_EPSON = 0x04B8
private const val ACAO_PERMISSAO = "br.manutencao.reset.PERMISSAO_USB"

class UsbErro(msg: String) : Exception(msg)

/** Interface de impressora (classe 7) com os endpoints bulk de entrada e saída. */
private fun interfaceImpressora(d: UsbDevice): Triple<UsbInterface, UsbEndpoint, UsbEndpoint?>? {
    for (i in 0 until d.interfaceCount) {
        val itf = d.getInterface(i)
        if (itf.interfaceClass != UsbConstants.USB_CLASS_PRINTER) continue
        var saida: UsbEndpoint? = null
        var entrada: UsbEndpoint? = null
        for (e in 0 until itf.endpointCount) {
            val ep = itf.getEndpoint(e)
            if (ep.type != UsbConstants.USB_ENDPOINT_XFER_BULK) continue
            if (ep.direction == UsbConstants.USB_DIR_OUT) saida = ep else entrada = ep
        }
        if (saida != null) return Triple(itf, saida, entrada)
    }
    return null
}

object UsbEpson {
    fun listar(ctx: Context): List<UsbDevice> {
        val um = ctx.getSystemService(Context.USB_SERVICE) as UsbManager
        return um.deviceList.values.filter { it.vendorId == VENDOR_EPSON && interfaceImpressora(it) != null }
    }

    /** Pede permissão ao usuário (janela do Android) e espera a resposta. */
    suspend fun pedirPermissao(ctx: Context, d: UsbDevice): Boolean {
        val um = ctx.getSystemService(Context.USB_SERVICE) as UsbManager
        if (um.hasPermission(d)) return true
        return suspendCancellableCoroutine { cont ->
            val rec = object : BroadcastReceiver() {
                override fun onReceive(c: Context, i: Intent) {
                    if (i.action != ACAO_PERMISSAO) return
                    try { ctx.unregisterReceiver(this) } catch (_: Exception) { }
                    if (cont.isActive) cont.resume(um.hasPermission(d))
                }
            }
            val filtro = IntentFilter(ACAO_PERMISSAO)
            if (Build.VERSION.SDK_INT >= 33) ctx.registerReceiver(rec, filtro, Context.RECEIVER_NOT_EXPORTED)
            else ctx.registerReceiver(rec, filtro)
            val flags = if (Build.VERSION.SDK_INT >= 31) PendingIntent.FLAG_MUTABLE else 0
            val pi = PendingIntent.getBroadcast(ctx, 0, Intent(ACAO_PERMISSAO).setPackage(ctx.packageName), flags)
            um.requestPermission(d, pi)
            cont.invokeOnCancellation { try { ctx.unregisterReceiver(rec) } catch (_: Exception) { } }
        }
    }

    fun abrir(ctx: Context, d: UsbDevice): UsbPorta {
        val um = ctx.getSystemService(Context.USB_SERVICE) as UsbManager
        val (itf, saida, entrada) = interfaceImpressora(d) ?: throw UsbErro("Interface de impressora não encontrada")
        val con = um.openDevice(d) ?: throw UsbErro("Sem permissão para abrir a impressora")
        if (!con.claimInterface(itf, true)) { con.close(); throw UsbErro("Impressora ocupada por outro app") }
        return UsbPorta(con, itf, saida, entrada)
    }
}

class UsbPorta(
    private val con: UsbDeviceConnection,
    private val itf: UsbInterface,
    private val saida: UsbEndpoint,
    private val entrada: UsbEndpoint?,
) : Porta, java.io.Closeable {

    override fun write(dados: ByteArray): Int {
        var enviado = 0
        while (enviado < dados.size) {
            val n = minOf(16384, dados.size - enviado)
            val pedaco = dados.copyOfRange(enviado, enviado + n)  // sem a variante com offset (só existe a partir do Android 9)
            val r = con.bulkTransfer(saida, pedaco, n, 5000)
            if (r <= 0) throw UsbErro("Falha ao enviar pela USB")
            enviado += r
        }
        return enviado
    }

    override fun read(timeoutMs: Int): ByteArray {
        val ep = entrada ?: return ByteArray(0)
        val buf = ByteArray(maxOf(ep.maxPacketSize, 512) * 8)
        val n = con.bulkTransfer(ep, buf, buf.size, timeoutMs)
        return if (n > 0) buf.copyOf(n) else ByteArray(0)
    }

    /** Device ID IEEE 1284 pela requisição de classe GET_DEVICE_ID. */
    fun deviceId(): String {
        val buf = ByteArray(1024)
        val n = con.controlTransfer(0xA1, 0, 0, itf.id shl 8, buf, buf.size, 3000)
        if (n < 2) return ""
        val tam = minOf(((buf[0].toInt() and 0xFF) shl 8) or (buf[1].toInt() and 0xFF), n)
        return String(buf, 2, maxOf(0, tam - 2), Charsets.ISO_8859_1)
    }

    override fun close() {
        try { con.releaseInterface(itf) } catch (_: Exception) { }
        con.close()
    }
}
